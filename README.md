# Telegram Üye Çekme Botu

Telegram gruplarından üye çekip başka bir gruba ekleyen bot.

## Özellikler

- 📱 Birden fazla Telegram hesabı ekleme (numara + kod + 2FA)
- 👥 Gruptaki tüm üyeleri çekme
- 💬 Grupta mesaj atan benzersiz kullanıcıları çekme
- ➕ Kullanıcı adı ile hedef gruba üye ekleme
- 🔄 Hesap ban yediğinde otomatik sonraki hesaba geçiş
- ⏭ Daha önce eklenen üyeleri atlama
- 📊 Detaylı işlem raporu

## Kurulum

### Gereksinimler

- Python 3.11+
- Telegram Bot Token (@BotFather'dan)
- Telegram API ID ve API Hash (https://my.telegram.org)
- Admin Telegram User ID

### Yerel Kurulum

```bash
git clone https://github.com/KULLANICI/telegram-member-scraper.git
cd telegram-member-scraper
pip install -r requirements.txt
cp .env.example .env
# .env dosyasını düzenleyin
python main.py
```

### Render Deploy

1. GitHub'a push edin
2. Render Dashboard'da "New > Background Worker" seçin
3. GitHub reposunu bağlayın
4. Environment Variables ekleyin:
   - `BOT_TOKEN`
   - `ADMIN_ID`
   - `API_ID`
   - `API_HASH`
5. Deploy edin

## Kullanım

1. `/start` komutu ile botu başlatın
2. "Hesap Ekle" ile Telegram hesaplarınızı ekleyin
3. "Kaynak Grup Ayarla" ile üyelerin çekileceği grubu belirleyin
4. "Hedef Grup Ayarla" ile üyelerin ekleneceği grubu belirleyin
5. "Tarama Başlat" ile kullanıcıları tarayın (tüm üyeler veya mesaj atanlar)
6. "Üye Eklemeyi Başlat" ile ekleme işlemini başlatın

## Notlar

- Her ekleme arasında 30-60 saniye bekleme süresi vardır
- Ban yiyen hesap otomatik olarak atlanır
- Daha önce eklenen kullanıcılar tekrar denenmez
- İşlem sonunda detaylı rapor verilir
