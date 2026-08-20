from flask import Flask, request, jsonify, render_template
from threading import Thread
import uuid
from flask_cors import CORS
from datetime import datetime, timedelta
import time
import json
import traceback

from trademark import IPIndiaTMR as ip
app = Flask(__name__)
# Enable CORS for all routes
CORS(app)

import threading

sessions = {}
session_lock = threading.Lock()

source_url1 = "trademark_search"


@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "service": "home",
        "status": "Running",
        "Trademark_search":f"/{source_url1}",
    })

@app.route(f"/{source_url1}/login", methods=["POST"])
def trademark_search():

    data = request.get_json()

    if not data.get("mobile_no"):
        return jsonify({
            "status": False,
            "message": "mobile_no is required"
        }), 400
    session_id = str(uuid.uuid4())
    tm = ip()
    tm.open()
    res=tm.send_otp(data["mobile_no"])
    with session_lock:
        sessions[session_id] = {
    "driver": tm,
    "busy": False,
    "created_at": datetime.now(),
    "last_activity": datetime.now()
    }
    msg, code = (("OTP sent successfully", 200) if res else ("Error Throw in OTP Sender", 422))

    return jsonify({
        "status": res,
        "message": msg,
        "session_id": session_id
    }), code
    


@app.route(f"/{source_url1}/verify_otp", methods=["POST"])
def verify_otp():

    data = request.get_json()

    session_id = data.get("session_id")
    otp = data.get("otp")

    if len(otp)==6:
        pass
    else:
        return jsonify({
        "message": f"OTP is Invalid. OTP is Six Letters Contains But You sended {len(otp)}"
    }) ,400
    with session_lock:
        session = sessions.get(session_id)
    if session is None:
        return jsonify({
            "status": False,
            "message": "Invalid Session"
        }), 404
    # session["last_activity"] = datetime.now()
    with session_lock:
        session["last_activity"] = datetime.now()
    tm = session["driver"]

    check=tm.verify_otp(otp)
    if check:
        pass
    elif check==None:
        return jsonify({
        "status": True,
        "message": "OTP is Invalid So retry"
    }) ,401
    else:
        return jsonify({
        "status": True,
        "message": "Invalid OTP So Retry it"
    }) ,401

    return jsonify({
        "status": True,
        "message": "OTP Verfied Home page Opened"
    }) ,200


@app.route(f"/{source_url1}/search", methods=["POST"])
def scrape():

    data = request.get_json(force=True)

    required = [
        "session_id",
        "word",
        "class_no"
    ]

    for key in required:
        if not data.get(key):
            return jsonify({
                "status": False,
                "message": f"{key} is required"
            }),400

    session_id = data["session_id"]

    with session_lock:
        session = sessions.get(session_id)

    if session is None:
        return jsonify({
            "status": False,
            "message": "Invalid Session"
        }), 404

    with session_lock:
        if session["busy"]:
            return jsonify({
                "status":False,
                "message":"Search already running"
            }),409
        session["busy"] = True

    try:

        tm = session["driver"]

        with session_lock:
            session["last_activity"] = datetime.now()
        print(data.get("mail_id", ""),data.get("mail_name", ""))
        result = tm.phonetic_search(
            data["word"],
            data["class_no"],
            data.get("app_no", ""),
            data.get("report_date", ""),
            data.get("mail_id", ""),
            data.get("mail_name", ""),
            data.get("eng_id", "")
        )
        # result={'Total_Records': 32, 'mail_id': 'j0E9dZ8BHIoBz0g62aTZ'}
        with session_lock:
            session["last_activity"] = datetime.now()

        print("Final Response: ",result,end="\n")
        return jsonify({
            "status": True,
            "data": result
        })

    except Exception as e:
        traceback.print_exc()   # Prints the full error and line number in Flask console

        return jsonify({
            "status": False,
            "message": str(e)
        }), 500

    finally:
        with session_lock:
            session["busy"] = False
        

@app.route(f"/{source_url1}/logout", methods=["POST"])
def logout():

    data = request.get_json()

    session_id = data.get("session_id")

    with session_lock:
        session = sessions.pop(session_id, None)

    if session is None:
        return jsonify({
            "status": False,
            "message": "Invalid Session"
        }), 404

    try:
        session["driver"].close()
    except Exception as e:
        print(e)

    return jsonify({
        "status": True,
        "message": "Session closed"
    })


SESSION_TIMEOUT = 2 * 60 * 60  # 2 hours 


def cleanup_sessions():
    while True:

        time.sleep(30)

        now = datetime.now()

        expired = []

        with session_lock:

            for session_id, session in list(sessions.items()):

                last_activity = session.get("last_activity", now)

                idle = (now - last_activity).total_seconds()

                if idle >= SESSION_TIMEOUT and not session["busy"]:
                    try:
                        session["driver"].close()
                        print(f"[{now}][AUTO LOGOUT] {session_id}")
                    except Exception as e:
                        print(e)

                    expired.append(session_id)

            for session_id in expired:
                sessions.pop(session_id, None)


if __name__ == "__main__":

    Thread(
        target=cleanup_sessions,
        daemon=True
    ).start()
    app.run(
    host="0.0.0.0",
    port=80,
    debug=True,
    use_reloader=False
)

#     app.run(
#     host="0.0.0.0",
#     port=5000,
#     debug=True,
#     use_reloader=False
# )