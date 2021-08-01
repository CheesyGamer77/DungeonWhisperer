FROM python:3

WORKDIR /usr/src/bot

RUN apt update
RUN apt install -y ffmpeg

# install required dependencies
RUN pip3 install discord.py[voice]
RUN pip3 install -U cheesyutils
RUN pip3 install spotipy
RUN pip3 install -U dislash.py

# start bot
CMD ["python3", "bot.py"]