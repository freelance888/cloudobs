FROM python:3.9.13-alpine3.16 AS builder

WORKDIR /app
ADD requirements.txt .
# RUN apk add git curl gcc build-base libffi-dev openssh pyopenssl
# RUN pip3 install pip --upgrade && pip3 install -r requirements.txt
# RUN git clone https://github.com/amukhsimov/gdown.git && cd gdown && pip3 install .
