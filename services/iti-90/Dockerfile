# syntax=docker/dockerfile:1
#
# Build requirements.txt using pip-tools and write it to the host:
#   docker build --target requirements -t iti-90-reqs services/iti-90
#   docker run --rm -v ${PWD}/services/iti-90:/work iti-90-reqs
#
# Then build the service image:
#   docker build -t iti-90 services/iti-90

FROM python:3.11-slim@sha256:233de06753d30d120b1a3ce359d8d3be8bda78524cd8f520c99883bfe33964cf
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "main.py"]
