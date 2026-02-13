FROM public.ecr.aws/lambda/python:3.12

COPY requirements.txt .
RUN pip install --no-cache-dir mangum fastapi python-multipart boto3 strands-agents beautifulsoup4 requests pillow PyMuPDF playwright

# Install Playwright browsers (skip system deps for now)
RUN playwright install chromium

COPY app.py .
COPY services/ services/
COPY static/ static/

CMD ["app.handler"]
