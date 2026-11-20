FROM registry.access.redhat.com/ubi9/python-311
EXPOSE 8080
CMD ["python", "-m", "http.server", "8080", "--directory", "/data"]
