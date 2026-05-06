import requests, json, sys

# 1. Check ComfyUI sees the models
try:
    ckpts = requests.get('http://127.0.0.1:8188/models/checkpoints', timeout=5).json()
    vaes  = requests.get('http://127.0.0.1:8188/models/vae', timeout=5).json()
    print('Checkpoints:', ckpts.get('value', []))
    print('VAEs       :', vaes.get('value', []))
except Exception as e:
    print('ComfyUI not reachable:', e)
    print('Restart ComfyUI first, then run this again.')
    sys.exit(1)

# 2. Validate the workflow
wf = json.loads(open('src/workflows/image_gen.json', encoding='utf-8').read())
print('\nWorkflow node map:')
for nid, node in wf.items():
    if not nid.startswith('_'):
        ct = node.get('class_type', '?')
        print(f'  [{nid}] {ct}')

# 3. Cross-check ckpt_name exists
ckpt_name = wf.get('4', {}).get('inputs', {}).get('ckpt_name', '')
vae_name  = wf.get('8', {}).get('inputs', {}).get('vae_name', '')
print(f'\nWorkflow expects:')
print(f'  Checkpoint : {ckpt_name}')
print(f'  VAE        : {vae_name}')

installed_ckpts = ckpts.get('value', [])
installed_vaes  = vaes.get('value', [])

ckpt_ok = ckpt_name in installed_ckpts
vae_ok  = vae_name  in installed_vaes

print(f'\nComfyUI sees them?')
print(f'  Checkpoint : {"YES - ready to run!" if ckpt_ok else "NO - restart ComfyUI to refresh"}')
print(f'  VAE        : {"YES - ready to run!" if vae_ok  else "NO - restart ComfyUI to refresh"}')

if ckpt_ok and vae_ok:
    print('\n[READY] Run: python main.py --preset beach_run_720p --topic "Final Quality Test" --frames 12 --motion 120 --skip-lipsync')
else:
    print('\n[ACTION] Restart ComfyUI terminal, then run this check again.')
