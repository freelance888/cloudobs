FROM base-image
# USER obs

WORKDIR /app
ADD . .

RUN apk add git curl gcc build-base libffi-dev openssh
EXPOSE 5000

HEALTHCHECK --interval=20s --timeout=30s --start-period=10s --retries=3 CMD curl -f http://localhost:5000/healthcheck

CMD ["python3", "common_service.py"]
