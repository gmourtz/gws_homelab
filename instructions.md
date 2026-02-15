I am building a homelab where I will be fully managing it with ansible. I would like to flash the images below to - two micro sds for the rpis - one usb stick for the optiplex I am aiming to develop a reproducible solution where i can be managed as infrastructure as code with ansible (and communicate with ssh to the devices) How can i do this in the most efficient, well documented, reproducible and scalable way? those are the devices attached.



Name Mac address Information IP
rpi3 b8:27:eb:7c:73:db Raspberry Pi 3 B+: 906MiB System memory 192.168.1.145 ubuntu-24.04.3-preinstalled-server-arm64+raspi.img.xz
optiplex Dell OptiPlex 3040 Micro Core i5-6500T 6GB RAM 240GB SSD ubuntu-24.04.3-live-server-amd64.iso

So far, i have set up this ssh key: ssh-keygen -t ed25519 -a 64 -f ~/.ssh/id_ed25519_gws_homelab -C "gws_homelab"


since this is a homelab project it needs to be as lean as possible following all the best practices without over engineering.