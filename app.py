from flask import Flask, request, jsonify
from income_tax_login import IncomeTaxLogin
from Trademark_Public_search import TrademarkSession , push
import requests
import uuid
from flask_cors import CORS

app = Flask(__name__)

CORS(app)
SESSIONS = {}  # session_id -> TrademarkSession

@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "service": "home",
        "status": "Running",
        "IncometaxNotice":{ "login":"/login"
        }
    })


@app.route("/ITR/login", methods=["POST"])
def itr_login():

    body = request.get_json()

    pan = body.get("pan")
    password = body.get("password")

    if not pan or not password:
        return jsonify({
            "status": False,
            "message": "PAN and Password are required"
        }), 400

    try:

        client = IncomeTaxLogin()

        responses = client.login(pan=pan,password=password)

        return jsonify({
            "status": True,
            "responses": responses
        })

    except Exception as e:

        return jsonify({
            "status": False,
            "error": str(e)
        }), 500


@app.route("/trademark/login/", methods=["POST"])
def trademark_login():
    # accepts phone_no or email, creates a session, sends OTP, returns session_id
    body = request.get_json(force=True)
    identifier = body.get("phone_no") or body.get("email")
    if not identifier:
        return jsonify({"Status": "phone_no or email required", "Session_id": ""}), 400

    session_id = str(uuid.uuid4())
    tm_session = TrademarkSession(identifier)
    try:
        tm_session.send_otp()
    except Exception as e:
        return jsonify({"Status": str(e), "Session_id": ""}), 400

    SESSIONS[session_id] = tm_session
    return jsonify({"Status": "OTP sent", "Session_id": session_id})


@app.route("/trademark/otp/", methods=["POST"])
def otp_verify():
    # verifies OTP against the given session_id, starts keepalive on success
    body = request.get_json(force=True)
    session_id = body.get("session_id")
    otp = body.get("otp")
    tm_session = SESSIONS.get(session_id)
    if not tm_session:
        return jsonify({"status": "invalid session_id"}), 400

    result = tm_session.verify_otp(otp)
    if result.get("success"):
        return jsonify({"status": "Successfully verified"})
    return jsonify({"status": result.get("message", "verification failed")})


@app.route("/trademark/search/", methods=["POST"])
def search():
    body = request.get_json(force=True)
    session_id = body.get("session_id")
    word = body.get("word")
    class_no = body.get("class")

    tm_session = SESSIONS.get(session_id)
    if not tm_session or not tm_session.tab_token:
        return jsonify({"status": "not logged in"}), 400

    records = tm_session.search(word, class_no)

    # Push one by one (no threads) — waits for each ES push to finish
    # before moving to the next record, so nothing is in flight at once.
    for record in records:
        push(record)

    total = len(records)
    token_id = records[0]["tokenid"] if records else ""

    return jsonify({
        "status": "Successfully fetched",
        "total": total,
        "token_id": token_id,
        "results": records
    })


@app.route("/trademark/report/", methods=["POST"])
def generate_report():
    body = request.get_json(force=True)

    session_id = body.get("session_id")
    app_no = body.get("app_no")
    report_date = body.get("report_date")
    mail_id = body.get("to_mail")
    mail_name = body.get("to_name")
    eng_id = body.get("engagement_id")

    tm_session = SESSIONS.get(session_id)

    if not tm_session:
        return jsonify({
            "status": "invalid session_id"
        }), 400

    try:
        result = tm_session.generate_report(
            app_no=app_no,
            report_date=report_date,
            mail_id=mail_id,
            mail_name=mail_name,
            eng_id=eng_id
        )

        return jsonify({
            "status": "success",
            "report_id": result["report_id"],
            "response": result["response"]
        })

    except Exception as e:
        return jsonify({
            "status": "failed",
            "message": str(e)
        }), 500



@app.route("/trademark/pushtoes/", methods=["POST"])
def push_to_es():
    # standalone push to OpenSearch, independent of session_id / login.
    # accepts either a single record object {...} or a list of records [{...}, {...}]
    body = request.get_json(force=True)

    if body is None:
        return jsonify({"status": "invalid or empty JSON body"}), 400

    records = body if isinstance(body, list) else [body]

    pushed = 0
    for record in records:
        push(record)
        pushed += 1

    return jsonify({
        "status": "Successfully pushed",
        "total": pushed
    })


@app.route("/trademark/logout/", methods=["POST"])
def logout():
    # closes the portal tab session and removes it from memory
    body = request.get_json(force=True)
    session_id = body.get("session_id")
    tm_session = SESSIONS.pop(session_id, None)
    if not tm_session:
        return jsonify({"status": "invalid session_id"}), 400

    tm_session.logout()
    return jsonify({"status": "logged out"})




if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8000,
        debug=True
    )
