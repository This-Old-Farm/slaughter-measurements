# for testing and documentation only. this is deployed to bare metal
FROM ubuntu:26.04

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 \
      python3-serial \
      python3-evdev \
      udev

COPY listen.py   /usr/local/bin/listen-slaughter-measurements
COPY shout.py    /usr/local/bin/shout-slaughter-measurements
COPY simulate.py /usr/local/bin/simulate-slaughter-measurements

RUN chmod +x /usr/local/bin/listen-slaughter-measurements \
             /usr/local/bin/shout-slaughter-measurements \
             /usr/local/bin/simulate-slaughter-measurements

ENTRYPOINT ["shout-slaughter-measurements"]
