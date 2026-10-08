# Dot Telegram Bridge — Developed by Revothin Yacht Electrical System.
"""Let the user enter the runtime key locally and connect the approved tunnel."""
import json
import os
import subprocess
import sys

from bridge import ROOT, load_env
from setup_telegram import token_dialog

ALIAS = 'revo-telegram'
CLIENT = ROOT / 'data' / 'tools' / 'tunnel-client' / 'tunnel-client.exe'
KEY_FILE = ROOT / 'data' / 'control-plane.key'


def main():
    load_env()
    tunnel_id = os.environ.get('OPENAI_TUNNEL_ID', '').strip()
    if not tunnel_id.startswith('tunnel_') or any(character.isspace() for character in tunnel_id):
        raise ValueError('Kendi tünel kimliğinizi .env içindeki OPENAI_TUNNEL_ID alanına yazın.')
    if not CLIENT.exists():
        raise ValueError('Yerel tünel istemcisi bulunamadı.')
    if not KEY_FILE.exists():
        key = token_dialog('Revo — Tünel çalışma anahtarı',
            'OpenAI Platform’da oluşturduğun Revo Telegram Tunnel API anahtarını yapıştır (Ctrl+V).',
            'Devam et: anahtar bu PC’de data/control-plane.key dosyasına kaydedilir; yalnız OpenAI tüneline giriş için kullanılır.')
        if key is None:
            print('İptal edildi.')
            return
        if not key.startswith('sk-') or len(key) < 25 or any(character.isspace() for character in key):
            raise ValueError('API anahtarı biçimi geçersiz. Telegram bot tokenını değil, Platform API anahtarını girin.')
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with KEY_FILE.open('x', encoding='utf-8') as output:
            output.write(key)
        del key
    print('Onaylanan Revo tüneli yerel köprüye bağlanıyor…', flush=True)
    command = [str(CLIENT), 'runtimes', 'connect', '--alias', ALIAS,
        '--tunnel-id', tunnel_id, '--mcp-server-url', 'http://127.0.0.1:8765/mcp',
        '--runtime-api-key', 'file:' + str(KEY_FILE), '--profile', ALIAS,
        '--profile-dir', str(ROOT / 'data' / 'tunnel-profile'), '--json']
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
    if result.returncode:
        print('Tünel bağlantısı tamamlanamadı. Anahtarın Tunnels Read + Use izinlerini kontrol edin. Anahtar yerel dosyada kaldı.')
        return
    status = subprocess.run([str(CLIENT), 'runtimes', 'status', ALIAS, '--json'],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    if status.returncode:
        print('Tünel başlatıldı; sağlık kontrolü henüz doğrulanamadı.')
        return
    try:
        health = json.loads(status.stdout)
    except json.JSONDecodeError:
        print('Sağlık çıktısı okunamadı; kontrol gerekli.')
        return
    if not health.get('healthy') or not health.get('ready'):
        print('Tünel süreci başlatıldı, ancak henüz hazır değil. İstemci sağlık durumunu kontrol edin.')
        return
    print('Tünel hazır. ChatGPT’de özel MCP eklentisine kendi tünel kimliğinizi ekleyin.')
    print('Revo’nun uçtan uca yanıt verdiği henüz doğrulanmadı.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Kurulum durduruldu.')
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

