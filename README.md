# Telegram Otomatik Duyuru Botu

Bu bot, admin onaylı kullanıcıların Telegram Client API ile hesap eklemesini, grupları içe aktarmasını, tekil grup bağlantısı eklemesini, duyuru metni ve gönderim sıklığı ayarlamasını sağlar. Duyurular, eklenen kullanıcı hesabı üzerinden kayıtlı gruplara gönderilir.

## Özellikler

- Inline keyboard tabanlı Türkçe menü.
- Telefon, doğrulama kodu ve iki aşamalı doğrulama şifresiyle Telethon hesabı ekleme.
- Hesaptaki grup ve megagroup dialoglarını listeleyip tümünü ekleme.
- `t.me/...` veya davet bağlantısıyla tekil grup ekleme.
- Grup silme, duyuru metni ayarlama/silme ve dakika bazında sıklık ayarı.
- Yeni kullanıcıları admin onayına sunma; onay, red ve kullanıcı silme.
- SQLite veritabanı ve Telegram session dosyaları için kalıcı disk desteği.

## Çalıştırma

```bash
cp .env.example .env
# .env dosyasını doldur
pip install -r requirements.txt
python main.py
```

## Render

Render üzerinde Docker web service olarak çalışır. `BOT_TOKEN`, `API_ID`, `API_HASH` ve `ADMIN_ID` gizli environment variable olarak tanımlanmalıdır. `/var/data` kalıcı diske bağlanır; SQLite veritabanı ve Telethon session dosyaları bu dizinde tutulur.

## Güvenlik

Bot token, API hash, API ID ve kullanıcı session dosyaları kaynak koduna yazılmamalıdır. Telegram hesabı session dosyaları kişisel erişim yetkisi taşıdığı için yalnızca kalıcı ve erişimi kısıtlı depolama alanında saklanmalıdır. Kullanıcı bu bilgiler sohbette açıklandığı için deploy sonrasında bot tokenı, GitHub tokenı ve Render API anahtarını yenilemelidir.

## Kullanım

Admin `/start` ile doğrudan kullanabilir. Diğer kullanıcılar ilk girişte admin onayı bekler. Onay sonrası `/start` ile menü açılır. Duyuru hesabı Telegram gruplarında mesaj gönderme yetkisine sahip olmalıdır.
