from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from datetime import datetime
import re
import random

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
def send_push_notification(title, body):
    message = messaging.Message(
        notification=messaging.Notification(
            title=title,
            body=body
        ),
        topic='todos'  # Flutter debe suscribirse a este topic
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

        # 🔔 ENVIAR NOTIFICACIÓN
        send_push_notification(
            title="Nuevo DTC registrado",
            body=f"Código(s): {', '.join(data['dtc'])}"
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

        # 🔔 ENVIAR NOTIFICACIÓN
        send_push_notification(
            title="Nuevo DTC simulado",
            body=f"Código(s): {', '.join(cleaned)}"
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
# INICIAR SERVIDOR
# -----------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
