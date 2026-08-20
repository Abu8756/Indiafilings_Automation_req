from flask import Flask, request, jsonify
from income_tax_login import IncomeTaxLogin

app = Flask(__name__)


@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "service": "home",
        "status": "Running",
        "IncometaxNotice":{ "login":"/login"
        }
    })


@app.route("/login", methods=["POST"])
def login():

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


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=1212,
        debug=True
    )