FROM public.ecr.aws/lambda/python:3.12

COPY requirements.txt .
RUN pip install --no-cache-dir mangum fastapi python-multipart boto3 "botocore[crt]" strands-agents beautifulsoup4 requests pillow PyMuPDF

COPY app.py .
COPY services/ services/
COPY static/ static/

CMD ["app.handler"]
