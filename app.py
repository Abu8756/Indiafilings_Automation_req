from flask import Flask, request, jsonify

app = Flask(__name__)



@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "service": "home",
        "status": "Running",
        "Trademark_search":"suma",
    })



if __name__ == "__main__":

     app.run(
     host="0.0.0.0",
     port=5000,
     debug=True,
     use_reloader=False
 )