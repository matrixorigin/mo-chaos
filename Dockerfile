FROM python:3.12
WORKDIR /root
COPY . .
RUN pip install -r requirements.txt
ENTRYPOINT python main.py