import requests
import time
import telegram
from telegram.error import RetryAfter

API_TOKEN = '7368689270:AAHawUIxldwE3kUplEFM0E55wQSCoNRPNzQ'
CHAT_ID = '@Newtoken_pump'

bot = telegram.Bot(token=API_TOKEN)

storico_token = {}
notificati_trending = set()

def trova_token_nuovi():
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    try:
        response = requests.get(url, headers={"Accept": "*/*"})
        if response.status_code != 200:
            print("Errore HTTP:", response.status_code)
            return []

        dati = response.json()
        nuovi = []

        for token in dati:
            token_address = token.get("tokenAddress")
            volume = token.get("volume", {}).get("h5", 0)

            if not token_address:
                continue

            if token_address not in storico_token:
                storico_token[token_address] = volume
                token["stato"] = "nuovo"
                nuovi.append(token)
            else:
                volume_prec = storico_token[token_address]
                if volume > volume_prec * 1.5 and token_address not in notificati_trending:
                    storico_token[token_address] = volume
                    token["stato"] = "trending"
                    notificati_trending.add(token_address)
                    nuovi.append(token)

        return nuovi

    except Exception as e:
        print("Errore:", e)
        return []

def invia_messaggi(token_list):
    for token in token_list:
        nome = token.get("description", "Nuovo Token")
        chain = token.get("chainId", "N/A")
        link = token.get("url", "")
        stato = token.get("stato", "nuovo")

        if stato == "trending":
            testo = (
                "🔥 *TOKEN IN TRENDING!* 🔥\n"
                f"Chain: {chain}\n"
                f"Nome: {nome}\n"
                f"[Guarda il grafico]({link})"
            )
        else:
            testo = (
                "Nuovo token rilevato:\n"
                f"Chain: {chain}\n"
                f"Nome: {nome}\n"
                f"[Apri pagina token]({link})"
            )

        try:
            bot.send_message(chat_id=CHAT_ID, text=testo, parse_mode="Markdown")
            time.sleep(1.5)  # rallenta per evitare flood
        except RetryAfter as e:
            print(f"Flood control: attesa di {e.retry_after} secondi...")
            time.sleep(e.retry_after)
        except Exception as errore:
            print("Errore invio messaggio:", errore)

if __name__ == "__main__":
    while True:
        nuovi_token = trova_token_nuovi()
        if nuovi_token:
            invia_messaggi(nuovi_token)
        time.sleep(30)
      
