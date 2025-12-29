from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from datetime import datetime
import re
import random
import requests
import os
import json
from generar_texto import generar_informe_ia

app = Flask(__name__)

# # -----------------------------
# # INICIALIZAR FIREBASE
# # -----------------------------
# cred = credentials.Certificate("serviceAccountKey.json")
# firebase_admin.initialize_app(cred)
# db = firestore.client()

# -----------------------------
# INICIALIZAR FIREBASE (desde ENV VAR si existe)
# -----------------------------
firebase_cred_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")

if firebase_cred_json:
    # La variable debe contener el JSON completo (string)
    try:
        cred_dict = json.loads(firebase_cred_json)
        cred = credentials.Certificate(cred_dict)
    except Exception as e:
        print("Error parseando FIREBASE_SERVICE_ACCOUNT_JSON:", e)
        raise
else:
    # Modo fallback (útil para desarrollo local si tienes el archivo)
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

        # unique = remove_duplicates_from_firestore()

        # ENVIAR NOTIFICACIÓN
        send_push_notification(
            title="Nuevo DTC registrado",
            body=f"Código(s): {', '.join(data['dtc'])}",
            codigo=data["dtc"][0] 
            
        )

        return jsonify({
            "status": "ok",
            "saved": data,
            # "unique_after_cleanup": unique
        }), 200

    except Exception as e:
        print("ERROR en /obd:", str(e))
        return jsonify({"error": str(e)}), 500

# -----------------------------
# ENDPOINT: OBTENER DTC ÚNICOS
# -----------------------------
@app.route('/data', methods=['GET'])
def get_data_full():
    try:
        docs = db.collection("obd_data").stream()
        registros = []
        for doc in docs:
            data = doc.to_dict()
            if "dtc" in data and isinstance(data["dtc"], list):
                for codigo in data["dtc"]:
                    registros.append({
                        "codigo": codigo,
                        "timestamp": data.get("timestamp")
                    })

        return jsonify({
            "dtc_registros": registros,
            "count": len(registros)
        }), 200

    except Exception as e:
        print("ERROR en /data_full:", str(e))
        return jsonify({"error": str(e)}), 500



# -----------------------------
# ENDPOINT: SIMULAR DTC ALEATORIO
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
        # unique = remove_duplicates_from_firestore()

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
            # "unique_after_cleanup": unique
        }), 200
    except Exception as e:
        print("ERROR en /simulate:", str(e))
        return jsonify({"error": str(e)}), 500


# -----------------------------
# ENDPOINT: SIMULAR DTC ESPECÍFICO
# -----------------------------
@app.route('/create_dtc/<codigo>', methods=['GET'])
def simulate_specific_dtc(codigo):
    try:
        # Usar directamente el código enviado por la URL
        cleaned = clean_dtc_list([codigo])
        data = {"dtc": cleaned, "timestamp": datetime.now().isoformat()}
        db.collection("obd_data").add(data)
        # unique = remove_duplicates_from_firestore()

        # ENVIAR NOTIFICACIÓN
        send_push_notification(
            title="Nuevo DTC registrado",
            body=f"Código(s): {', '.join(cleaned)}",
            codigo=cleaned[0]
        )

        return jsonify({
            "status": "simulated",
            "received_raw": codigo,
            "generated_cleaned": cleaned,
            # "unique_after_cleanup": unique
        }), 200
    except Exception as e:
        print("ERROR en /create_dtc:", str(e))
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
            data["id"] = doc.id  
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
    

# -----------------------------------------
# ENDPOINT: ELIMINAR TODOS LOS INFORMES IA
# -----------------------------------------
@app.route('/ia_reports', methods=['DELETE'])
def delete_all_ia_reports():
    try:
        docs = db.collection("ia_reports").stream()

        eliminados = 0
        for doc in docs:
            doc.reference.delete()
            eliminados += 1

        return jsonify({
            "status": "ok",
            "deleted_reports": eliminados
        }), 200

    except Exception as e:
        print("ERROR en DELETE /ia_reports:", e)
        return jsonify({"error": str(e)}), 500

    
# -----------------------------------------
# ENDPOINT: GUARDAR / ACTUALIZAR INFORME IA
# -----------------------------------------
@app.route('/ia_reports', methods=['POST'])
def save_ia_report():
    try:
        data = request.get_json()

        if not data or "codigo" not in data or "informe" not in data:
            return jsonify({"error": "Falta codigo o informe"}), 400

        codigo = clean_string(data["codigo"])

        # Eliminar informes previos del mismo código
        old_docs = db.collection("ia_reports").where("codigo", "==", codigo).stream()
        for doc in old_docs:
            doc.reference.delete()

        # Insertar el nuevo informe
        db.collection("ia_reports").add({
            "codigo": codigo,
            "informe": data["informe"],
            "timestamp": datetime.now().isoformat()
        })

        return jsonify({"status": "updated", "codigo": codigo}), 200

    except Exception as e:
        print("ERROR guardando IA report:", e)
        return jsonify({"error": str(e)}), 500
    

# -----------------------------------------
# ENDPOINT: borrar TODOS los DTC 
# -----------------------------------------
@app.route('/borrar_dtc_todos', methods=['POST'])
def clear_history():
    try:
        docs = db.collection("obd_data").stream()
        batch = db.batch()
        count = 0

        for doc in docs:
            batch.delete(doc.reference)
            count += 1

        batch.commit()

        return jsonify({
            "status": "success",
            "message": "Historial de Firestore eliminado.",
            "deleted_count": count
        }), 200

    except Exception as e:
        print(f"ERROR /clear_history: {e}")
        return jsonify({"error": "Error borrando base de datos", "details": str(e)}), 500


# -----------------------------------------
# ENDPOINT: borrar CÓDIGOS DTC en la ECU
# -----------------------------------------   
@app.route('/reset_ecu', methods=['POST'])
def reset_ecu():
    esp32_url = "http://192.168.0.3/clear_obd"
    
    try:
        # Timeout corto (3s) para no dejar colgada la app si el carro está apagado
        r = requests.post(esp32_url, timeout=3)

        if r.status_code == 200:
            return jsonify({
                "status": "success",
                "message": "Comando enviado a la ECU correctamente."
            }), 200
        else:
            return jsonify({
                "status": "error",
                "message": f"ESP32 respondió con error: {r.status_code}"
            }), 502 # Bad Gateway (error del dispositivo remoto)

    except requests.exceptions.ConnectTimeout:
        return jsonify({
            "status": "error",
            "message": "Tiempo de espera agotado. ¿Está el ESP32 encendido?"
        }), 504 # Gateway Timeout
        
    except requests.exceptions.RequestException as e:
        # Cualquier otro error de conexión
        print(f"ERROR CONEXIÓN ESP32: {e}")
        return jsonify({
            "status": "error",
            "message": "No se pudo conectar con el ESP32."
        }), 503 # Service Unavailable

# -----------------------------
# INICIAR SERVIDOR
# -----------------------------
# if __name__ == '__main__':
#     app.run(host='0.0.0.0', port=5000, debug=True)
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
