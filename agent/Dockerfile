FROM python:3.12-slim

WORKDIR /agent
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV AGENT_CONFIG=config.yaml

CMD ["python", "agent.py"]
