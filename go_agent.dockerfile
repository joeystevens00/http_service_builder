FROM gocd/gocd-agent-ubuntu-18.04:v20.1.0
USER root
RUN apt-get update
RUN apt-get upgrade -y
RUN apt install -y docker.io vim-common python3-pip

WORKDIR /api_builder
COPY . /api_builder
RUN pip3 install -r requirements.txt
