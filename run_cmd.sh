#!/bin/bash

ssh localhost -p222
cd ~/omnichannel-crm/omnichannel-crm/
source venv/bin/activate
ln -sf /home/r/rusla9m5/.local/lib/libffi.so.6.0.4 /home/r/rusla9m5/.local/lib/libffi.so.6
export LD_LIBRARY_PATH=/home/r/rusla9m5/.local/lib:$LD_LIBRARY_PATH
python3 manage.py $@
