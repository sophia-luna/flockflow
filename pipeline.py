import json
import paho.mqtt.client as mqtt
from db_client import get_db_client

# Inicializa o cliente do banco garantindo a utilização de anon key e RLS
supabase = get_db_client()

# ---------------------------------------------------------
# 1. CONFIGURAÇÕES DO BROKER MQTT
# ---------------------------------------------------------
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_TOPIC = "ufg/flockflow/telemetria/galpao01"

# ---------------------------------------------------------
# 2. LÓGICA DE SANITIZAÇÃO (Data Pipeline & Outliers)
# ---------------------------------------------------------
def sanitizar_payload(dados):
    """
    Aplica a sanitização dos dados e remoção de outliers sensoriais,
    protegendo o banco contra falhas de hardware da camada Edge.
    """
    payload_limpo = {}
    
    # Validação estrutural básica
    if "galpao_id" not in dados or not dados["galpao_id"]:
        raise ValueError("Pacote descartado: 'galpao_id' ausente ou vazio.")
    payload_limpo["galpao_id"] = str(dados["galpao_id"])

    # Temperatura de bulbo seco: Aceita leituras entre -10°C e 60°C
    temp = dados.get("temperatura_c")
    if temp is not None and -10.0 <= float(temp) <= 60.0:
        payload_limpo["temperatura_c"] = float(temp)
        
    # Umidade relativa: 0% a 100%
    umidade = dados.get("umidade_perc")
    if umidade is not None and 0.0 <= float(umidade) <= 100.0:
        payload_limpo["umidade_perc"] = float(umidade)
        
    # Velocidade do Ar: Não pode ser negativa
    vento = dados.get("velocidade_ar_ms")
    if vento is not None and float(vento) >= 0.0:
        payload_limpo["velocidade_ar_ms"] = float(vento)
        
    # Gases e Particulados: Não podem ter concentração negativa
    nh3 = dados.get("nh3_ppm")
    if nh3 is not None and float(nh3) >= 0.0:
        payload_limpo["nh3_ppm"] = float(nh3)
        
    co2 = dados.get("co2_ppm")
    if co2 is not None and int(co2) >= 0:
        payload_limpo["co2_ppm"] = int(co2)
        
    poeira = dados.get("poeira_pm25")
    if poeira is not None and float(poeira) >= 0.0:
        payload_limpo["poeira_pm25"] = float(poeira)

    return payload_limpo

# ---------------------------------------------------------
# 3. LÓGICA DE ESCUTA (CALLBACKS)
# ---------------------------------------------------------
def on_connect(client, userdata, flags, rc):
    """Função acionada assim que o script conecta ao HiveMQ."""
    if rc == 0:
        print(f"✅ Conectado ao broker HiveMQ: {MQTT_BROKER}")
        client.subscribe(MQTT_TOPIC)
        print(f"📡 Escutando telemetria no tópico: {MQTT_TOPIC}...\n")
    else:
        print(f"❌ Falha na conexão. Código de erro: {rc}")

def on_message(client, userdata, msg):
    """Função acionada automaticamente toda vez que um novo pacote chega."""
    try:
        payload_texto = msg.payload.decode('utf-8')
        dados_brutos = json.loads(payload_texto)
        
        print(f"📦 Pacote bruto recebido da Borda:")
        print(json.dumps(dados_brutos, indent=2))

        # Passa o dado pelo funil de sanitização
        dados_telemetria = sanitizar_payload(dados_brutos)

        # Insere os dados na tabela do Supabase
        resposta = supabase.table("telemetria_avicola").insert(dados_telemetria).execute()
        
        print("☁️ Salvo no Supabase com sucesso após sanitização!\n")
        print("-" * 40)

    except ValueError as ve:
        print(f"⚠️ Falha de Validação: {ve}")
        print("-" * 40)
    except json.JSONDecodeError:
        print("⚠️ Erro Crítico: A mensagem recebida não é um JSON válido.")
        print("-" * 40)
    except Exception as e:
        print(f"⚠️ Erro inesperado ao processar ou salvar no banco: {e}")
        print("-" * 40)

# ---------------------------------------------------------
# 4. EXECUÇÃO DO WORKER
# ---------------------------------------------------------
# Instancia o cliente MQTT e vincula as funções de callback
worker = mqtt.Client()
worker.on_connect = on_connect
worker.on_message = on_message

print("Iniciando o Pipeline Edge-to-Cloud...")
# Conecta ao broker (tempo de keepalive de 60 segundos)
worker.connect(MQTT_BROKER, MQTT_PORT, 60)

# Mantém o script rodando infinitamente em background
worker.loop_forever()