#!/bin/bash

ssh localhost -p222
cd ~/omnichannel-crm/omnichannel-crm/
source venv/bin/activate
python3 manage.py $@
