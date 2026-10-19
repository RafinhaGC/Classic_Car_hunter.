# 🏁 Classic Car Hunter • Plataforma Multi-Sócio de Importação Sob Demanda

Plataforma web e agente automatizado para busca, avaliação logística e importação de carros clássicos e raros dos Estados Unidos para o Brasil.

## ✨ Principais Recursos

1. **Acesso Individual por Sócio (`/api/auth/login`)**:
   - Contas separadas para **Rafael Campos** e **Marçal Borborema** com senha individual, e-mail e número de WhatsApp próprios.
   - Credenciais iniciais padrão (alteráveis pelo painel em **⚙️ Sócios & Alertas**):
     - **Rafael Campos**: Usuário `rafael` (ou `socio1`) | Senha `123456`
     - **Marçal Borborema**: Usuário `marcal` (ou `socio2`) | Senha `123456`

2. **Buscas Sob Demanda (Qualquer Marca / Modelo / Ano / Cliente)**:
   - Cadastre encomendas de clientes com marca, modelo principal, palavras-chave de carroceria/variantes, faixa de ano, faixa de orçamento em USD e cidade/CEP base nos EUA (padrão: **Deerfield Beach, FL `33441`**).
   - Varredura simultânea em 4 portais:
     - **eBay Motors** (`ebay.com`)
     - **Autotrader Classics** (`classics.autotrader.com`)
     - **ClassicCars.com** (`classiccars.com`)
     - **Hemmings Classifieds** (`hemmings.com`)

3. **Agendamento Diário Automático & Filtro de Novidades Relevantes**:
   - Configure cada busca para rodar automaticamente **1x, 2x, 3x, 4x, 6x ou 12x por dia** ou em **horários fixos** (ex.: `08:00, 14:00, 20:00`).
   - Aba dedicada **🔥 Novidades Relevantes** destacando apenas carros novos com alto score ou reduções de preço.

4. **Alertas por WhatsApp e E-mail para o Sócio Responsável**:
   - Envia automaticamente (ou sob comando manual) os resultados para o **sócio que criou a busca** (ou para ambos os sócios).
   - **WhatsApp**: Suporte nativo no painel para **CallMeBot**, **Z-API**, **Evolution API**, **UltraMsg**, **Twilio** e **Webhook HTTP**, além de botão direto **Abrir no WhatsApp (`wa.me`)** com o relatório formatado.
   - **E-mail**: Disparo via **SMTP / Gmail (Senha de App)** com template HTML completo (fotos, preços em USD, estimativa nacionalizada em R$, distância até a Flórida e links diretos).

5. **Simulador de Importação EUA → Brasil em Cada Veículo**:
   - Calcula automaticamente o custo estimado nacionalizado no Brasil considerando valor FOB (USD), frete interno de guincho nos EUA até a Flórida conforme a distância em milhas, frete marítimo, impostos de veículos antigos (>30 anos) e desembaraço aduaneiro.

---

## 🚀 Como Iniciar o Site

1. Dê dois cliques em [`start_hunter.bat`](file:///C:/Users/deias/.gemini/antigravity/scratch/classic-car-hunter/start_hunter.bat) ou execute no terminal:
   ```powershell
   C:\Users\deias\.gemini\antigravity\scratch\classic-car-hunter\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
   ```
2. Acesse `http://127.0.0.1:8080` no navegador.
