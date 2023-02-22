from __future__ import print_function
from flask import Flask

app = Flask(__name__)


@app.route("/init", methods=["POST"])
def init():
    return "#deprecated", 200


@app.route("/files", methods=["GET"])
def get_files():
    return "#deprecated", 500


@app.route("/healthcheck", methods=["GET"])
def healthcheck():
    return "", 200


if __name__ == "__main__":
    app.run("0.0.0.0", 7000)
