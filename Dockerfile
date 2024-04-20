FROM python:3.9

WORKDIR /app

COPY requirements.txt /app/

RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

RUN python3 manage.py check
RUN python3 manage.py makemigrations
RUN python3 manage.py migrate
