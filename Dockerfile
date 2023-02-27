FROM python:3.9.13-alpine3.16 AS builder
# USER obs

WORKDIR /app
ADD . .
RUN apk add git curl gcc build-base libffi-dev openssh
RUN pip3 install -r requirements.txt
RUN pip3 install pip --upgrade
RUN pip3 install pyopenssl --upgrade
RUN git clone https://github.com/amukhsimov/gdown.git && cd gdown && pip3 install .
RUN apt update && apt install tzdata -y
ENV TZ="Europe/Kiev"

EXPOSE 5000

HEALTHCHECK --interval=20s --timeout=30s --start-period=10s --retries=3 CMD curl -f http://localhost:5000/healthcheck

CMD ["python3", "common_service.py"]
