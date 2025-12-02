from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from datetime import datetime
import re
import random
from generar_texto import generar_informe_ia

app = Flask(__name__)

# -----------------------------
# INICIALIZAR FIREBASE
# -----------------------------
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# -----------------------------
# FUNCIONES AUXILIARES
# -----------------------------
def clean_string(s):
    return "".join(c for c in s if c.isalnum()).upper()

def is_valid_dtc(code):
    return bool(re.match(r'^[PCBU][0-9]{4,5}$', code))

def clean_dtc_list(dtc_list):
    cleaned = []
    for code in dtc_list:
        if not isinstance(code, str):
            continue
        c = clean_string(code)
        if is_valid_dtc(c):
            cleaned.append(c)
    return sorted(list(set(cleaned)))

def remove_duplicates_from_firestore():
    docs = db.collection("obd_data").stream()
    all_dtcs = []
    for doc in docs:
        data = doc.to_dict()
        if "dtc" in data and isinstance(data["dtc"], list):
            all_dtcs.extend(data["dtc"])
    unique_dtcs = clean_dtc_list(all_dtcs)

    # Borrar toda la colección
    batch = db.batch()
    for doc in db.collection("obd_data").stream():
        batch.delete(doc.reference)
    batch.commit()

    # Guardar solo DTC únicos
    for code in unique_dtcs:
        db.collection("obd_data").add({
            "dtc": [code],
            "timestamp": datetime.now().isoformat()
        })

    return unique_dtcs

# -----------------------------
# FUNCION PARA ENVIAR NOTIFICACIÓN FCM
# -----------------------------
def send_push_notification(title, body, codigo):
    message = messaging.Message(
        notification=messaging.Notification(
            title=title,
            body=body
        ),
        data={ "dtc": codigo },
        topic='todos',
        android=messaging.AndroidConfig(
            notification=messaging.AndroidNotification(
                click_action="FLUTTER_NOTIFICATION_CLICK"
            )
        )
    )
    response = messaging.send(message)
    print('Notificación enviada:', response)

# -----------------------------
# ENDPOINT: RECIBIR OBD
# -----------------------------
@app.route('/obd', methods=['POST'])
def obd_data():
    try:
        data = request.get_json(force=True, silent=True)
        if not data or "dtc" not in data or not isinstance(data["dtc"], list):
            return jsonify({"error": "Datos inválidos"}), 400

        data["dtc"] = clean_dtc_list(data["dtc"])
        if len(data["dtc"]) == 0:
            return jsonify({"error": "No se recibieron códigos válidos"}), 400

        data["timestamp"] = datetime.now().isoformat()
        db.collection("obd_data").add(data)

        unique = remove_duplicates_from_firestore()

        # ENVIAR NOTIFICACIÓN
        send_push_notification(
            title="Nuevo DTC registrado",
            body=f"Código(s): {', '.join(data['dtc'])}",
            codigo=data["dtc"][0] 
            
        )

        return jsonify({
            "status": "ok",
            "saved": data,
            "unique_after_cleanup": unique
        }), 200

    except Exception as e:
        print("ERROR en /obd:", str(e))
        return jsonify({"error": str(e)}), 500

# -----------------------------
# ENDPOINT: OBTENER DTC ÚNICOS
# -----------------------------
@app.route('/data', methods=['GET'])
def get_data():
    try:
        docs = db.collection("obd_data").stream()
        all_dtcs = []
        for doc in docs:
            data = doc.to_dict()
            if "dtc" in data and isinstance(data["dtc"], list):
                all_dtcs.extend(data["dtc"])
        unique_dtcs = clean_dtc_list(all_dtcs)
        return jsonify({"unique_dtc": unique_dtcs, "count": len(unique_dtcs)}), 200
    except Exception as e:
        print("ERROR en /data:", str(e))
        return jsonify({"error": str(e)}), 500

# -----------------------------
# ENDPOINT: SIMULAR DTC
# -----------------------------
@app.route('/simulate', methods=['GET'])
def simulate_data():
    try:
        prefixes = ["P", "C", "B", "U"]
        prefix = random.choice(prefixes)
        numbers = str(random.randint(1000, 99999))
        generated = prefix + numbers
        cleaned = clean_dtc_list([generated])
        data = {"dtc": cleaned, "timestamp": datetime.now().isoformat()}
        db.collection("obd_data").add(data)
        unique = remove_duplicates_from_firestore()

        # ENVIAR NOTIFICACIÓN
        send_push_notification(
            title="Nuevo DTC registrado",
            body=f"Código(s): {', '.join(cleaned)}",
            codigo=cleaned[0] 
        )

        return jsonify({
            "status": "simulated",
            "generated_raw": generated,
            "generated_cleaned": cleaned,
            "unique_after_cleanup": unique
        }), 200
    except Exception as e:
        print("ERROR en /simulate:", str(e))
        return jsonify({"error": str(e)}), 500
    
# -----------------------------
# ENDPOINT: GUARDAR CONFIGURACIÓN DEL VEHÍCULO
# -----------------------------
@app.route('/vehicle', methods=['POST'])
def save_vehicle():
    try:
        data = request.get_json(force=True, silent=True)

        if not data:
            return jsonify({"error": "Datos inválidos"}), 400

        required = ["marca", "modelo", "año", "vin"]
        for key in required:
            if key not in data or not str(data[key]).strip():
                return jsonify({"error": f"Falta el campo: {key}"}), 400

        # Guardar en Firestore
        db.collection("vehicle_config").document("config").set({
            "marca": data["marca"],
            "modelo": data["modelo"],
            "anio": data["año"],
            "vin": data["vin"],
            "timestamp": datetime.now().isoformat()
        })

        return jsonify({"status": "ok", "vehicle_saved": data}), 200

    except Exception as e:
        print("ERROR en /vehicle:", str(e))
        return jsonify({"error": str(e)}), 500
    

# -----------------------------------------
# ENDPOINT: OBTENER VEHÍCULO GUARDADO
# -----------------------------------------
@app.route('/vehicle', methods=['GET'])
def get_vehicle():
    try:
        doc = db.collection("vehicle_config").document("config").get()

        if not doc.exists:
            return jsonify({"exists": False}), 200

        data = doc.to_dict()
        data["exists"] = True

        return jsonify(data), 200

    except Exception as e:
        print("ERROR en GET /vehicle:", e)
        return jsonify({"exists": False, "error": str(e)}), 500
    
    
# -----------------------------------------
# ENDPOINT: OBTENER VEHÍCULO GUARDADO
# -----------------------------------------
@app.route('/ia/<codigo>', methods=['GET'])
def ia_dtc(codigo):
    try:
        # Obtener datos del vehículo
        doc = db.collection("vehicle_config").document("config").get()
        if not doc.exists:
            return jsonify({"error": "No hay vehículo guardado"}), 400
        
        vehiculo = doc.to_dict()

        # Validar código
        codigo = clean_string(codigo)
        if not is_valid_dtc(codigo):
            return jsonify({"error": "Código DTC inválido"}), 400

        # Generar informe usando Gemini
        informe = generar_informe_ia(codigo, vehiculo)

        # Guardar historial (opcional)
        db.collection("ia_reports").add({
            "codigo": codigo,
            "vehiculo": vehiculo,
            "informe": informe,
            "timestamp": datetime.now().isoformat()
        })

        return jsonify({
            "codigo": codigo,
            "vehiculo": vehiculo,
            "informe": informe
        }), 200

    except Exception as e:
        print("ERROR IA:", e)
        return jsonify({"error": str(e)}), 500
    
    
# -----------------------------------------
# ENDPOINT: BORRAR SOLO UN CÓDIGO DTC
# -----------------------------------------
@app.route('/delete_dtc/<codigo>', methods=['DELETE'])
def delete_dtc(codigo):
    try:
        # Limpiar y validar el código
        codigo = clean_string(codigo)

        if not is_valid_dtc(codigo):
            return jsonify({"error": "Código DTC inválido"}), 400

        docs = db.collection("obd_data").stream()

        modified_docs = 0
        removed_docs = 0

        for doc in docs:
            data = doc.to_dict()

            if "dtc" in data and codigo in data["dtc"]:
                nueva_lista = [c for c in data["dtc"] if c != codigo]

                if len(nueva_lista) == 0:
                    # El documento queda vacío → eliminar
                    doc.reference.delete()
                    removed_docs += 1
                else:
                    # Actualizar documento sin el DTC eliminado
                    doc.reference.update({"dtc": nueva_lista})
                    modified_docs += 1

        return jsonify({
            "status": "ok",
            "deleted_code": codigo,
            "updated_docs": modified_docs,
            "removed_empty_docs": removed_docs
        }), 200

    except Exception as e:
        print("ERROR en DELETE /delete_dtc:", e)
        return jsonify({"error": str(e)}), 500
    


# -----------------------------------------
# ENDPOINT: OBTENER HISTORIAL COMPLETO IA_REPORTS
# -----------------------------------------
@app.route('/ia_reports', methods=['GET'])
def get_ia_reports():
    try:
        docs = db.collection("ia_reports").order_by("timestamp", direction=firestore.Query.DESCENDING).stream()

        historial = []
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id  # opcional: útil para borrar por ID
            historial.append(data)

        return jsonify({
            "count": len(historial),
            "reports": historial
        }), 200

    except Exception as e:
        print("ERROR en GET /ia_reports:", e)
        return jsonify({"error": str(e)}), 500


# -----------------------------------------
# ENDPOINT: ELIMINAR INFORME IA POR CÓDIGO
# -----------------------------------------
@app.route('/ia_reports/<codigo>', methods=['DELETE'])
def delete_ia_report(codigo):
    try:
        codigo = clean_string(codigo)

        # validar código
        if not is_valid_dtc(codigo):
            return jsonify({"error": "Código DTC inválido"}), 400

        docs = db.collection("ia_reports").where("codigo", "==", codigo).stream()

        eliminados = 0
        for doc in docs:
            doc.reference.delete()
            eliminados += 1

        return jsonify({
            "status": "ok",
            "deleted_code": codigo,
            "deleted_reports": eliminados
        }), 200

    except Exception as e:
        print("ERROR en DELETE /ia_reports/<codigo>:", e)
        return jsonify({"error": str(e)}), 500

# -----------------------------
# INICIAR SERVIDOR
# -----------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)