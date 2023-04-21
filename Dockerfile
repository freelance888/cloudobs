# FROM base-image
FROM python:3.9.13-alpine3.16 AS builder
# USER obs

WORKDIR /app
ADD . .

RUN apk add git curl gcc build-base libffi-dev openssh
RUN wget https://files.pythonhosted.org/packages/b7/a4/f40c5a989c2b9381ebe3a19be28a15469a9233c83a82ca86f8abe455f41b/pandas-1.5.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
RUN pip3 install ./pandas-1.5.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
RUN pip3 install -r requirements.txt
RUN git clone https://github.com/amukhsimov/gdown.git && cd gdown && pip3 install .
ENV TZ="Europe/Kiev"
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
RUN pip3 install pip --upgrade
RUN pip3 install pyopenssl --upgrade

RUN apk add git curl gcc build-base libffi-dev openssh
RUN pactl load-module module-null-sink sink_name=monitor_sink sink_properties=device.description=monitor_sink
RUN pactl load-module module-null-sink sink_name=obs_sink sink_properties=device.description=obs_sink

EXPOSE 5000

HEALTHCHECK --interval=20s --timeout=30s --start-period=10s --retries=3 CMD curl -f http://localhost:5000/healthcheck

CMD ["python3", "common_service.py"]
