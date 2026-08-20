from flask import Flask, request, jsonify, render_template
from flask_cors import CORS


#from trademark import IPIndiaTMR as ip
app = Flask(__name__)
# Enable CORS for all routes
CORS(app)


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