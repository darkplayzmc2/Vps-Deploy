Clone the repository
git clone https://github.com/darkplayzmc2/Vps-Deploy

cd vps-deploy

Update VPS packages
apt update

Setup environment file
cp test.env .env

Install Python pip
apt install python3-pip -y

Check Python version
python -v

Fix pip permission issue
mkdir -p ~/.config/pip && echo -e "[global]\nbreak-system-packages = true" > ~/.config/pip/pip.conf

Install dependencies
pip install -r requirements.txt

Install Docker
apt install docker.io -y

Check running Docker containers
docker ps

Create systemd service
sudo nano /etc/systemd/system/javixbot.service

Paste this exactly:

[Unit]
Description=Javix Bot Discord Bot
After=network.target

[Service]
User=root
WorkingDirectory=/root
ExecStart=/usr/bin/python3 /root/bot.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target

Reload and start the bot
sudo systemctl daemon-reload
sudo systemctl restart javixbot

(Optional: start on boot)
sudo systemctl enable javixbot

