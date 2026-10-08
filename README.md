# Dot Telegram Bridge

**Revothin Yacht Electrical System tarafından geliştirilmiştir.**

Telegram botunuzu kendi ChatGPT dot asistanınıza bağlayan Windows üzerinde çalışan MCP köprüsü. Soruları Telegram'dan gönderin; dot, erişimi olan araçlarla araştırıp sonucu aynı özel sohbete iletsin.

Bu proje bağımsızdır; OpenAI veya Telegram tarafından geliştirilmemiştir.

## Özellikler

- Tek bir eşleştirilmiş sahibin özel Telegram mesajlarını kabul eder.
- Kalıcı SQLite kuyruk, imzalı MCP olay teslimi ve tekrar gönderim koruması.
- Hızlı alındı bildirimi ve aynı mesajda güncellenen süre sayacı.
- Teslim, okunma ve tamamlanma durumları; bilinmeyen kalan süreyi uydurmaz.
- Yerel HTTP sunucusu ve OpenAI Secure MCP Tunnel bağlantısı.

Bilgisayar ve e-posta erişimini dot'un ayrı bağlantıları sağlar. Bu köprü BlueMail'e doğrudan bağlanmaz; tüm PC verilerine erişimi veya otomatik posta izlemesini tek başına sağlamaz.

## Gereksinimler

Windows, Python 3.10+ ve Tkinter; kendi Telegram botunuz; özel MCP eklentileri ve dot olay görevlerine erişebilen ChatGPT hesabınız; kendi Secure MCP Tunnel kimliğiniz ve Tunnels Read + Use izinli çalışma anahtarınız. PC işleri için dot'un bilgisayar bağlantısı ve ilgili araç izinleri gerekir.

## Kurulum

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest -v
```

1. Resmî @BotFather ile bot oluşturun. `Kurulum.cmd` ile tokenı yerel maskeli alana girin. Ctrl+V ve sağ tık yapıştırma desteklenir. Tek kullanımlık mesajı botunuza gönderip hesabınızı doğrulayın. Kurulum `.env` oluşturur; mevcut dosyanın üzerine yazmaz.
2. `.env` içine kendi `OPENAI_TUNNEL_ID=tunnel_...` satırınızı ekleyin.
3. `Revo_Sunucu.cmd` ile köprüyü başlatın. Yerel adres `http://127.0.0.1:8765/mcp`.
4. Resmî [tunnel-client](https://github.com/openai/tunnel-client/releases) Windows paketini indirin, yayımlanan SHA256 ile doğrulayın ve paket dosyalarını `data/tools/tunnel-client/` altına çıkartın.
5. `Tunel_Kurulum.cmd` ile Platform çalışma anahtarınızı maskeli alana girin. Anahtar `data/control-plane.key` içine yerel olarak kaydedilir.
6. ChatGPT Eklentiler → + → Özel MCP sunucusu oluştur ekranında **Dot Telegram Bridge** adını ve kendi tünel kimliğinizi kullanın. Bağlantı: Tünel; yerel sunucuda kimlik doğrulama yoktur, erişim özel tünel tarafından sağlanır.
7. Eklentiyi dot'unuzda kullanın ve şu talimatı gönderin:

```text
Dot Telegram Bridge eklentisinin telegram.message.created olayını izle.
telegram_inbox ile bekleyen istekleri oku. Sorularımı uygun bağlı araçlarla
araştır ve her isteği aynı update_id için telegram_reply ile Türkçe yanıtla.
Köprü alındı bildirimi ve süre sayacını kendisi gönderir; ayrıca ack gönderme.
Basit soruları kısa yanıtla. Gereken izinleri açıkça belirt ve mevcut onay
kurallarına uy. Aboneliği telegram_bridge_status ile doğrula.
```

Dot adı size aittir; kodda kişisel bot veya tünel kimliği yoktur. `Baslat.cmd` eski taşıma dinleyicisidir; MCP sunucusuyla aynı anda çalıştırmayın. Tek bot için tek polling süreci kullanın. Mevcut webhook otomatik olarak kaldırılmaz.

## Durum sayacı ve araçlar

Alındı mesajı yaklaşık 15 saniyede bir düzenlenir; beş dakikadan sonra aralık 60 saniyedir. Yanıt gönderilince sayaç durur. Okunma bilgisi `telegram_inbox` erişimine dayanır; modelin hâlâ çalıştığının heartbeat'i değildir. Kalan süre bilinmiyorsa tahmin üretilmez. PC, köprü ve tünel açık olmalıdır.

| Araç | İşlev |
| --- | --- |
| telegram_inbox | Bekleyen ilk 100 metin isteğini okur. |
| telegram_reply | İsteği yanıtlar ve tamamlar. |
| telegram_notify | Sabit anahtarla tek seferlik bildirim gönderir. |
| telegram_bridge_status | Kuyruk ve abonelik sayılarını gösterir. |

MCP 2.0 (`2026-07-28`) olay keşfi, abonelik ve iptal desteklenir. Yeni abonelik eski mesajları yeniden yayınlamaz; bekleyenler inbox aracından okunur.

## Güvenlik ve sınırlar

- `.env`, `data/`, anahtarlar, mesaj geçmişi ve yerel profiller Git'e dahil edilmez.
- Yerel kimlik bilgileri ve mesajlar diskte şifrelenmemiştir; paylaşmayın.
- Sunucuyu internete açık adrese bağlamayın. Localhost ve özel tünel kullanın.
- Callback için HTTPS, genel IP kontrolü, sabit DNS sonucu, TLS doğrulaması ve HMAC imzası kullanılır.
- Sonucu belirsiz gönderimler otomatik tekrar edilmez.
- Yalnız metin ve özel sohbet desteklenir; medya, gruplar ve düzenlemeler desteklenmez.
- Teslim en fazla 8 girişimdir; geçmiş replay ve anahtar rotasyonunda eski anahtar örtüşmesi yoktur.
- Süresi dolan anahtarı yenileyin. Abonelik tek başına başarılı yanıt kanıtı değildir.

## Sahiplik

**Revothin Yacht Electrical System**

Copyright © 2026 Revothin Yacht Electrical System. Tüm hakları saklıdır. Ayrı bir açık kaynak lisansı verilmemiştir. Üçüncü taraf bağımlılıklar kendi lisanslarına tabidir.

## Resmî belgeler

- [Telegram Bot API](https://core.telegram.org/bots/api)
- [MCP Events](https://developers.openai.com/plugins/build/mcp-events)
- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [Özel MCP sunucusu](https://developers.openai.com/api/docs/guides/custom-mcp-server)
