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

# Inicializar firebase
firebase_cred_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")

if firebase_cred_json:
    try:
        cred_dict = json.loads(firebase_cred_json)
        cred = credentials.Certificate(cred_dict)
    except Exception as e:
        print("Error parseando FIREBASE_SERVICE_ACCOUNT_JSON:", e)
        raise
else:
    cred = credentials.Certificate("serviceAccountKey.json")

firebase_admin.initialize_app(cred)
db = firestore.client()

# Funciones auxiliares
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

# Función para enviar notificacin FCM
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

# Endpoint: Recibir datos OBD
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

        # ENVIAR NOTIFICACIÓN
        send_push_notification(
            title="Nuevo DTC registrado",
            body=f"Código(s): {', '.join(data['dtc'])}",
            codigo=data["dtc"][0] 
            
        )

        return jsonify({
            "status": "ok",
            "saved": data,
        }), 200

    except Exception as e:
        print("ERROR en /obd:", str(e))
        return jsonify({"error": str(e)}), 500

# Endopint: Obtener DTC únicos
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

# Endpoint: Simular DTC aleatorio
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
        }), 200
    except Exception as e:
        print("ERROR en /simulate:", str(e))
        return jsonify({"error": str(e)}), 500


# Endpoint: Simular DTC específico
@app.route('/create_dtc/<codigo>', methods=['GET'])
def simulate_specific_dtc(codigo):
    try:
        cleaned = clean_dtc_list([codigo])
        data = {"dtc": cleaned, "timestamp": datetime.now().isoformat()}
        db.collection("obd_data").add(data)

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
        }), 200
    except Exception as e:
        print("ERROR en /create_dtc:", str(e))
        return jsonify({"error": str(e)}), 500

# Endpoint: Guardar configuración del vehículo
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
    

# Endpoint: Obtener configuración del vehículo
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
    
    
# Endpoint: Generar informe IA para un código DTC
@app.route('/ia/<codigo>', methods=['GET'])
def ia_dtc(codigo):
    try:
        doc = db.collection("vehicle_config").document("config").get()
        if not doc.exists:
            return jsonify({"error": "No hay vehículo guardado"}), 400
        
        vehiculo = doc.to_dict()

        codigo = clean_string(codigo)
        if not is_valid_dtc(codigo):
            return jsonify({"error": "Código DTC inválido"}), 400

        informe = generar_informe_ia(codigo, vehiculo)

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
    
    
# Endpoint: Eliminar un código DTC de todos los documentos
@app.route('/delete_dtc/<codigo>', methods=['DELETE'])
def delete_dtc(codigo):
    try:
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
                    doc.reference.delete()
                    removed_docs += 1
                else:
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
    
# Endpoint: Obtener todos los informes IA
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

# Endpoint: Eliminar informes IA por código DTC
@app.route('/ia_reports/<codigo>', methods=['DELETE'])
def delete_ia_report(codigo):
    try:
        codigo = clean_string(codigo)

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
    
# Endpoint: Eliminar todos los informes IA
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

# Endpoint: Eliminar codigos DTC de Firestore
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

# Endpoint: Eliminar DTC de la ECU
@app.route('/commands/clear_dtc', methods=['POST'])
def command_clear_dtc():
    db.collection("commands").document("ecu").set({
        "action": "CLEAR_DTC",
        "status": "pending",
        "requested_at": datetime.utcnow().isoformat() + "Z"
    })
    return jsonify({"status": "ok"}), 200

# Endpoint: Obtener estado del comando a la ECU
@app.route('/commands/status', methods=['GET'])
def command_status():
    doc = db.collection("commands").document("ecu").get()
    if not doc.exists:
        return jsonify({"exists": False}), 200
    return jsonify(doc.to_dict()), 200

# Endpoint: Confirmar ejecución del comando a la ECU
@app.route('/commands/confirm', methods=['POST'])
def command_confirm():
    data = request.get_json()
    status = data.get("status", "error")

    db.collection("commands").document("ecu").set({
        "action": None,
        "status": status,
        "completed_at": datetime.utcnow().isoformat() + "Z"
    })

    return jsonify({"status": "updated"}), 200

# Ejecutar la aplicación
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
