# Aster DEX Perpetual Trading Bot 🤖

Bot Telegram che fa trading automatico sui **perpetual futures di Aster DEX**
(API Pro V3), con gestione del rischio integrata e modalità simulazione.

## Come funziona

- **Strategia**: incrocio di medie mobili esponenziali (EMA 9/21) con filtro
  RSI. Segnale LONG quando la EMA veloce incrocia al rialzo la lenta,
  SHORT quando incrocia al ribasso.
- **Gestione del rischio**: ogni trade viene aperto con stop-loss e
  take-profit basati sull'ATR; la dimensione della posizione è calcolata
  in base alla percentuale di saldo che vuoi rischiare. Se la perdita
  giornaliera supera la soglia configurata, il bot si ferma da solo.
- **Controllo via Telegram**: avvii, fermi e monitori tutto dal telefono.
- **Modalità simulazione (default)**: con `DRY_RUN=true` il bot simula i
  trade senza toccare i tuoi fondi. Mettila a `false` solo quando sei pronto.

## Setup

### 1. Crea il bot Telegram
1. Scrivi a [@BotFather](https://t.me/BotFather) → `/newbot` → copia il token.
2. Scrivi a [@userinfobot](https://t.me/userinfobot) per ottenere il tuo user ID.

### 2. Crea l'API wallet su Aster
1. Vai su <https://www.asterdex.com/en/api-wallet> e seleziona il tab **Pro API**.
2. Registra un API wallet (agent): otterrai **indirizzo signer** e **chiave privata**.
3. La chiave privata resta solo nel tuo `.env` — **non condividerla mai**.

### 3. Configura e avvia
```bash
git clone https://github.com/valentino500x/Telegram-Token-Bot.git
cd Telegram-Token-Bot
pip install -r requirements.txt
cp .env.example .env   # poi compila i valori
python BOT.py
```

Su Telegram: `/start` per la panoramica, `/run` per avviare il trading.

## Comandi

| Comando | Descrizione |
| --- | --- |
| `/run` / `/stop` | avvia / ferma il trading automatico |
| `/status` | stato, ultimo segnale e posizione |
| `/position` | dettagli della posizione aperta |
| `/close` | chiude la posizione e cancella gli ordini |
| `/balance` | saldo del conto futures |
| `/price` | prezzo corrente del simbolo |
| `/pnl` | PnL realizzato oggi |
| `/symbol ETHUSDT` | cambia simbolo |
| `/leverage 5` | cambia leva (1–50) |
| `/risk 0.5` | rischio per trade in % |

## Configurazione

Tutte le opzioni sono in [`.env.example`](.env.example): simbolo, timeframe,
leva, parametri della strategia (EMA/RSI/ATR), limiti di rischio e modalità
dry-run.

## ⚠️ Avvertenze

- Il trading di perpetual futures con leva è **ad altissimo rischio**: puoi
  perdere tutto il capitale. Nessuna strategia garantisce profitti.
- Testa sempre prima in modalità `DRY_RUN=true` e parti con importi piccoli.
- Usa un API wallet dedicato con solo i fondi che sei disposto a rischiare.
- Questo software è fornito "così com'è", senza alcuna garanzia (licenza MIT).
