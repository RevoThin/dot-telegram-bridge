# Dot Telegram Bridge — Developed by Revothin Yacht Electrical System.
"""Interactive local Telegram setup; secrets never appear in chat or command arguments."""
import re
import secrets
import sys
import time

from bridge import ROOT, Telegram


def token_dialog(title='Revo_AI — Bot tokenı', instruction='BotFather tokenını buraya yapıştırın (Ctrl+V veya sağ tık → Yapıştır).', note='Token yalnızca bu PC’de kullanılır. Sohbete gönderilmez.'):
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(title)
    root.geometry('610x285')
    root.resizable(False, False)
    value = tk.StringVar()
    result = []
    ttk.Label(root, text=instruction,
              wraplength=570).pack(padx=20, pady=(20, 12), anchor='w')
    field = ttk.Entry(root, textvariable=value, show='*', width=74)
    field.pack(padx=20, fill='x')
    menu = tk.Menu(root, tearoff=0)
    menu.add_command(label='Yapıştır', command=lambda: field.event_generate('<<Paste>>'))
    menu.add_command(label='Tümünü seç', command=lambda: field.selection_range(0, 'end'))

    def popup(event):
        field.focus_set()
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    field.bind('<Button-3>', popup)
    visible = tk.BooleanVar(value=False)
    ttk.Checkbutton(root, text='Tokenı göster', variable=visible,
                    command=lambda: field.configure(show='' if visible.get() else '*')).pack(padx=20, pady=10, anchor='w')
    ttk.Label(root, text=note, wraplength=570).pack(padx=20, anchor='w')

    def submit():
        result.append(value.get().strip())
        root.destroy()

    ttk.Button(root, text='Devam et', command=submit).pack(pady=15)
    root.bind('<Return>', lambda event: submit())
    field.focus_set()
    root.mainloop()
    return result[0] if result else None


def pairing_dialog(challenge):
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title('Revo_AI — Telegram eşleştirme')
    root.geometry('610x220')
    root.resizable(False, False)
    confirmed = []
    ttk.Label(root, text='Bu mesajı kopyalayıp Telegram’da botun özel sohbetine yapıştırın.',
              wraplength=570).pack(padx=20, pady=20, anchor='w')
    value = tk.StringVar(value=challenge)
    field = ttk.Entry(root, textvariable=value, state='readonly', width=74)
    field.pack(padx=20, fill='x')

    def copy():
        root.clipboard_clear()
        root.clipboard_append(challenge)
        copy_button.configure(text='Kopyalandı')

    copy_button = ttk.Button(root, text='Mesajı kopyala', command=copy)
    copy_button.pack(pady=12)

    def proceed():
        confirmed.append(True)
        root.destroy()

    ttk.Button(root, text='Telegram’dan gönderdim, devam et', command=proceed).pack(pady=6)
    root.mainloop()
    return bool(confirmed)


def pairing_candidate(updates, challenge):
    for update in updates:
        message = update.get('message', {})
        sender = message.get('from', {})
        chat = message.get('chat', {})
        user_id = sender.get('id', 0)
        if (message.get('text') == challenge and chat.get('type') == 'private'
                and isinstance(user_id, int) and user_id > 0
                and chat.get('id') == user_id and not sender.get('is_bot', True)):
            return sender
    return None


def main():
    if not sys.stdin.isatty():
        raise ValueError('Kurulumu kendi terminal pencerenizde çalıştırın.')
    destination = ROOT / '.env'
    if destination.exists():
        raise ValueError('.env zaten var; mevcut ayarlar değiştirilmedi.')
    print('Revo_AI Telegram kurulumu\nToken giriş penceresi açılıyor…')
    token = token_dialog()
    if token is None:
        print('Kurulum iptal edildi.')
        return
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
        raise ValueError('Token biçimi geçersiz.')
    telegram = Telegram(token)
    bot = telegram.call('getMe')
    print('Bağlanılacak bot: @' + bot['username'])
    if input('Bu senin Revo_AI botun mu? (evet/hayır): ').strip().lower() != 'evet':
        print('Kurulum iptal edildi.')
        return
    if telegram.call('getWebhookInfo').get('url'):
        raise ValueError('Bu botta webhook etkin. Mevcut bağlantı değiştirilmedi.')
    challenge = '/start revo_' + secrets.token_hex(12)
    if not pairing_dialog(challenge):
        print('Kurulum iptal edildi.')
        return
    print('Eşleştirme mesajı aranıyor…')
    deadline = time.monotonic() + 90
    candidate = None
    while time.monotonic() < deadline:
        updates = telegram.call('getUpdates', {'timeout': 10, 'allowed_updates': ['message']})
        candidate = pairing_candidate(updates, challenge)
        if candidate:
            break
        time.sleep(1)
    if candidate is None:
        raise ValueError('Eşleştirme mesajı bulunamadı. Bot sohbetine yazdığınızı kontrol edip tekrar çalıştırın.')
    print('Telegram kullanıcı kimliği:', candidate['id'])
    print('Hesap:', repr(candidate.get('username') or candidate.get('first_name', '')))
    if input('Bu senin hesabın mı? (evet/hayır): ').strip().lower() != 'evet':
        print('Kurulum iptal edildi.')
        return
    # Exclusive creation prevents overwriting a configuration created during pairing.
    with destination.open('x', encoding='utf-8') as output:
        output.write(f'TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_OWNER_USER_ID={candidate["id"]}\nTELEGRAM_OWNER_CHAT_ID={candidate["id"]}\n')
    print('\nAyarlar yerel .env dosyasına kaydedildi. Bu dosya tokenı içerir; paylaşmayın.')
    telegram.send(candidate['id'], 'Revo_AI Telegram köprüsü eşleştirildi. Revo ve BlueMail bağlantısı henüz hazır değil.')
    print('Test mesajı gönderildi. Mesaj dinleyicisini başlatmak için Baslat.cmd dosyasını açın.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nKurulum durduruldu.')
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

