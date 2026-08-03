import io
from datetime import datetime, timezone
from fastapi.responses import StreamingResponse
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.ensemble import RandomForestRegressor
from db_client import get_db_client

supabase = get_db_client()
app = FastAPI(title="API de Inteligência Avícola")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def obter_dias_lote(galpao_id: str) -> int:
    resp_lote = supabase.table("lotes_avicolas").select("data_alojamento").eq("galpao_id", galpao_id).eq("ativo", True).limit(1).execute()
    if resp_lote.data:
        data_alojamento = datetime.fromisoformat(resp_lote.data[0]['data_alojamento'].replace('Z', '+00:00'))
        return max(1, (datetime.now(timezone.utc) - data_alojamento).days + 1)
    return 1

@app.get("/api/v1/metadata/{galpao_id}")
def obter_metadata_galpao(galpao_id: str):
    try:
        # 1. PRIMEIRO: Descobre a fase do ciclo para aplicar a regra térmica correta
        resp_lote = supabase.table("lotes_avicolas") \
            .select("data_alojamento, quantidade_aves") \
            .eq("galpao_id", galpao_id) \
            .eq("ativo", True) \
            .limit(1) \
            .execute()
        
        if resp_lote.data and len(resp_lote.data) > 0:
            lote = resp_lote.data[0]
            data_alojamento_str = lote['data_alojamento']
            quantidade_aves = lote['quantidade_aves']
            
            data_alojamento = datetime.fromisoformat(data_alojamento_str.replace('Z', '+00:00'))
            hoje = datetime.now(timezone.utc)
            dia_ciclo = max(1, (hoje - data_alojamento).days + 1)
            
            # RN02: Ajuste Dinâmico do Limite de Estresse Térmico
            if dia_ciclo <= 10:
                fase = "Pré-aquecimento / Inicial"
                limite_temp_critica = 35.0 # Filhotes aguentam e precisam de calor
            elif dia_ciclo <= 28:
                fase = "Crescimento"
                limite_temp_critica = 32.0 # Aves maiores sofrem acima de 32°C
            else:
                fase = "Terminação / Final"
                limite_temp_critica = 32.0
        else:
            # Galpão Vazio Sanitário
            return {
                "galpao_id": galpao_id,
                "dia_ciclo": 0,
                "fase": "Sem Lote (Vazio Sanitário)",
                "quantidade_aves": 0,
                "status_geral": "Higienização / Manutenção",
                "cor_status": "slate"
            }

        # 2. SEGUNDO: Busca a telemetria e valida contra os limites dinâmicos
        resp_tel = supabase.table("telemetria_avicola") \
            .select("nh3_ppm, poeira_pm25, temperatura_c") \
            .eq("galpao_id", galpao_id) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        
        status_geral = "Operação Segura"
        cor = "emerald"
        
        if resp_tel.data:
            row = resp_tel.data[0]
            nh3 = row.get('nh3_ppm') or 0.0
            poeira = row.get('poeira_pm25') or 0.0
            temp = row.get('temperatura_c') or 0.0
            
            # O semáforo agora respeita a idade da ave (limite_temp_critica)
            if nh3 >= 20.0 or poeira > 60.0 or temp > limite_temp_critica:
                status_geral = "Alerta Crítico na Borda"
                cor = "rose"

        return {
            "galpao_id": galpao_id,
            "dia_ciclo": dia_ciclo,
            "fase": fase,
            "quantidade_aves": quantidade_aves,
            "status_geral": status_geral,
            "cor_status": cor
        }
    except Exception as e:
        print(f"Erro em /metadata: {e}")
        return {
            "galpao_id": galpao_id,
            "dia_ciclo": 1,
            "fase": "Falha de Leitura",
            "quantidade_aves": 0,
            "status_geral": "Conexão Perdida",
            "cor_status": "slate"
        }

class SimulacaoRequest(BaseModel):
    potencia_exaustor_kw: float
    tarifa_kwh: float
    custo_fixo_ciclo: float
    receita_projetada: float

@app.post("/api/v1/simular/{galpao_id}")
def simular_cenario(galpao_id: str, payload: SimulacaoRequest):
    try:
        resp_telemetria = supabase.table("telemetria_avicola") \
            .select("created_at, nh3_ppm, poeira_pm25") \
            .eq("galpao_id", galpao_id).execute()
        
        if not resp_telemetria.data:
            return {"erro": "Sem dados de telemetria suficientes para simulação."}

        df = pd.DataFrame(resp_telemetria.data)
        df['exaustor_ligado'] = (df['nh3_ppm'] >= 20.0) | (df['poeira_pm25'] > 60.0)
        
        horas_exaustor_ligado = (df['exaustor_ligado'].sum() * 5) / 60.0

        # NOVO CÁLCULO DE VALOR GERADO (SIMULAÇÃO)
        custo_energia = horas_exaustor_ligado * payload.potencia_exaustor_kw * payload.tarifa_kwh
        
        # 1. Aves Salvas (Estimativa: cada hora de mitigação cirúrgica salva ~15 aves do estresse agudo)
        aves_salvas = int(horas_exaustor_ligado * 15)
        valor_aves_salvas = aves_salvas * 15.00 # R$ 15 por unidade
        
        # 2. Energia Poupada (O sistema inteligente roda menos que o timer "cego")
        horas_poupadas = horas_exaustor_ligado * 1.5 
        economia_energia = horas_poupadas * payload.potencia_exaustor_kw * payload.tarifa_kwh
        
        dinheiro_salvo = valor_aves_salvas + economia_energia

        return {
            "galpao_id": galpao_id,
            "modo": "simulacao",
            "kpis_operacionais": {
                "horas_ventilacao_emergencia": round(horas_exaustor_ligado, 2),
                "custo_energetico_mitigacao_brl": round(custo_energia, 2),
                "aves_salvas": aves_salvas
            },
            "kpis_financeiros": {
                "dinheiro_salvo_brl": round(dinheiro_salvo, 2)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/galpoes")
def listar_galpoes():
    try:
        # Busca diretamente da tabela mestre de galpões
        resp = supabase.table("galpoes").select("id, nome").order("nome").execute()
        return {"galpoes": resp.data if resp.data else []}
    except Exception as e:
        print(f"Erro em /galpoes: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/kpis/{galpao_id}")
def obter_kpis_financeiros(galpao_id: str):
    try:
        resp_config = supabase.table("configuracoes_operacionais") \
            .select("*").eq("galpao_id", galpao_id).execute()
        
        if not resp_config.data:
            raise HTTPException(status_code=404, detail="Configurações não encontradas.")
            
        config = resp_config.data[0]
        potencia_kw = config['potencia_exaustor_kw']
        tarifa = config['tarifa_kwh']

        resp_telemetria = supabase.table("telemetria_avicola") \
            .select("created_at, nh3_ppm, poeira_pm25") \
            .eq("galpao_id", galpao_id).execute()
            
        if not resp_telemetria.data:
            return {"mensagem": "Sem dados suficientes para os KPIs."}

        df = pd.DataFrame(resp_telemetria.data)
        df['exaustor_ligado'] = (df['nh3_ppm'] >= 20.0) | (df['poeira_pm25'] > 60.0)
        
        horas_exaustor_ligado = (df['exaustor_ligado'].sum() * 5) / 60.0

        # NOVO CÁLCULO DE VALOR GERADO (TEMPO REAL)
        custo_energia = horas_exaustor_ligado * potencia_kw * tarifa
        aves_salvas = int(horas_exaustor_ligado * 15)
        valor_aves_salvas = aves_salvas * 15.00 
        
        horas_poupadas = horas_exaustor_ligado * 1.5
        economia_energia = horas_poupadas * potencia_kw * tarifa
        
        dinheiro_salvo = valor_aves_salvas + economia_energia

        payload = {
            "galpao_id": galpao_id,
            "kpis_operacionais": {
                "horas_ventilacao_emergencia": round(horas_exaustor_ligado, 2),
                "custo_energetico_mitigacao_brl": round(custo_energia, 2),
                "aves_salvas": aves_salvas
            },
            "kpis_financeiros": {
                "dinheiro_salvo_brl": round(dinheiro_salvo, 2)
            }
        }
        return payload

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/telemetria/{galpao_id}")
def obter_historico_telemetria(galpao_id: str):
    try:
        # Tenta buscar incluindo a velocidade do ar e o CO2
        resp = supabase.table("telemetria_avicola") \
            .select("created_at, temperatura_c, umidade_perc, nh3_ppm, co2_ppm, poeira_pm25, velocidade_ar_ms") \
            .eq("galpao_id", galpao_id) \
            .order("created_at", desc=True) \
            .limit(30) \
            .execute()
        
        dados = resp.data[::-1] if resp.data else []
        return {"galpao_id": galpao_id, "historico": dados}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/eventos/{galpao_id}")
def obter_eventos_borda(galpao_id: str):
    try:
        resp = supabase.table("telemetria_avicola") \
            .select("created_at, nh3_ppm, poeira_pm25") \
            .eq("galpao_id", galpao_id) \
            .order("created_at", desc=True) \
            .limit(50) \
            .execute()
        
        eventos = []
        if resp.data:
            for row in resp.data:
                nh3 = row.get('nh3_ppm') or 0.0
                poeira = row.get('poeira_pm25') or 0.0
                
                if nh3 >= 20.0 or poeira > 60.0:
                    eventos.append({
                        "timestamp": row.get('created_at'),
                        "motivo": f"NH3: {float(nh3):.1f} ppm | PM2.5: {float(poeira):.1f} µg/m³",
                        "nivel": "Crítico / Alerta"
                    })
                    
        return {"galpao_id": galpao_id, "eventos": eventos[:10]}
    except Exception as e:
        print(f"Erro detalhado em /eventos: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/previsao/{galpao_id}")
def prever_tendencia_ar(galpao_id: str):
    """
    RF06: Evolução para Modelo Não-Linear (Random Forest) para prever amônia.
    RN01: Gatilho de criticidade após 20º dia.
    """
    try:
        dia_ciclo = obter_dias_lote(galpao_id)
        
        # Ampliamos a janela de coleta para dar mais insumos ao modelo de árvores
        resp = supabase.table("telemetria_avicola") \
            .select("created_at, nh3_ppm, temperatura_c, velocidade_ar_ms") \
            .eq("galpao_id", galpao_id) \
            .order("created_at", desc=True).limit(300).execute()
            
        # VALIDAÇÃO 1: Garante que há dados suficientes antes de treinar e criar o DataFrame
        if not resp.data or len(resp.data) < 20:
            return {
                "galpao_id": galpao_id, 
                "risco_preditivo": "Normal", 
                "mensagem": "Aguardando volume de dados para treinamento do modelo.",
                "nh3_projetado": 0.0,
                "algoritmo": "N/A"
            }
            
        df = pd.DataFrame(resp.data)
        
        # VALIDAÇÃO 2: Garante que as colunas essenciais existam, mesmo se o sensor falhar (Evita KeyError)
        for col in ['temperatura_c', 'velocidade_ar_ms', 'nh3_ppm']:
            if col not in df.columns:
                df[col] = 0.0
        
        # CORREÇÃO PANDAS: Tratamento de nulos sem inplace=True e com fallback seguro
        media_vento = df['velocidade_ar_ms'].mean()
        df['velocidade_ar_ms'] = df['velocidade_ar_ms'].fillna(media_vento).fillna(0.0)
        df['temperatura_c'] = df['temperatura_c'].fillna(df['temperatura_c'].mean()).fillna(0.0)
        df['nh3_ppm'] = df['nh3_ppm'].fillna(0.0)
        
        # Variáveis Independentes (X) e Dependente (y)
        X = df[['temperatura_c', 'velocidade_ar_ms']]
        X.insert(0, 'dia_ciclo', dia_ciclo) 
        y = df['nh3_ppm']
        
        # Instanciação do Random Forest
        modelo = RandomForestRegressor(n_estimators=50, max_depth=5, random_state=42)
        modelo.fit(X, y)
        
        # Cenário preditivo
        temp_futura = df['temperatura_c'].iloc[0] + 1.0 
        vento_atual = df['velocidade_ar_ms'].iloc[0]
        
        # CORREÇÃO SCIKIT-LEARN: Previsão usando um DataFrame com os mesmos nomes de colunas
        X_futuro = pd.DataFrame(
            [[dia_ciclo, temp_futura, vento_atual]], 
            columns=['dia_ciclo', 'temperatura_c', 'velocidade_ar_ms']
        )
        nh3_previsto = modelo.predict(X_futuro)[0]
        
        # RN01: Gatilho de Criticidade Temporal
        alerta_temporal = " (CRITICIDADE ATIVA: Lote > 20 dias)" if dia_ciclo >= 20 else ""
        
        risco = "Baixo"
        msg = f"Random Forest: Saturação prevista sob controle ({nh3_previsto:.1f} ppm)."
        if nh3_previsto >= 20.0:
            risco = "Alto"
            msg = f"IA Preditiva: ALERTA! Projeção de {nh3_previsto:.1f} ppm de NH3 no curto prazo." + alerta_temporal
        elif nh3_previsto >= 15.0 or (dia_ciclo >= 20 and nh3_previsto >= 10.0):
            risco = "Moderado"
            msg = f"IA Preditiva: Curva de amônia em elevação ({nh3_previsto:.1f} ppm)." + alerta_temporal

        return {
            "galpao_id": galpao_id, 
            "risco_preditivo": risco, 
            "mensagem": msg, 
            "nh3_projetado": round(nh3_previsto, 2),
            "algoritmo": "RandomForestRegressor"
        }
    except Exception as e:
        print(f"Erro em /previsao capturado: {e}")
        # Retorna um fallback amigável ao invés do Erro 500
        return {
            "galpao_id": galpao_id, 
            "risco_preditivo": "Indisponível", 
            "mensagem": "Análise preditiva aguardando normalização da rede...", 
            "nh3_projetado": 0.0
        }

@app.get("/api/v1/alertas/{galpao_id}")
def checar_alertas_ativos(galpao_id: str):
    try:
        resp = supabase.table("telemetria_avicola") \
            .select("created_at, nh3_ppm, poeira_pm25, temperatura_c") \
            .eq("galpao_id", galpao_id) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        
        alertas = []
        if resp.data:
            row = resp.data[0]
            nh3 = row.get('nh3_ppm') or 0.0
            poeira = row.get('poeira_pm25') or 0.0
            temp = row.get('temperatura_c') or 0.0
            
            if nh3 >= 20.0:
                alertas.append({
                    "tipo": "Crítico",
                    "canal": "WhatsApp / Webhook",
                    "mensagem": f"Concentração de Amônia (NH3) em {nh3:.1f} ppm. Limite de segurança excedido!",
                    "timestamp": row.get('created_at')
                })
            if poeira > 60.0:
                alertas.append({
                    "tipo": "Aviso",
                    "canal": "Webhook Interno",
                    "mensagem": f"Nível de poeira PM2.5 elevado ({poeira:.1f} µg/m³).",
                    "timestamp": row.get('created_at')
                })
            if temp > 32.0:
                alertas.append({
                    "tipo": "Alerta Térmico",
                    "canal": "WhatsApp / SMS",
                    "mensagem": f"Temperatura interna crítica ({temp:.1f}°C). Risco de estresse térmico nas aves.",
                    "timestamp": row.get('created_at')
                })
                
        return {"galpao_id": galpao_id, "total_alertas": len(alertas), "alertas": alertas}
    except Exception as e:
        print(f"Erro em /alertas: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/exportar/{galpao_id}")
def exportar_relatorio_csv(galpao_id: str):
    try:
        resp = supabase.table("telemetria_avicola") \
            .select("created_at, temperatura_c, umidade_perc, nh3_ppm, co2_ppm, poeira_pm25") \
            .eq("galpao_id", galpao_id) \
            .order("created_at", desc=True) \
            .execute()
        
        if not resp.data:
            raise HTTPException(status_code=404, detail="Sem dados disponíveis para exportação.")
            
        df = pd.DataFrame(resp.data)
        
        # Padroniza os nomes das colunas para auditoria profissional
        df = df.rename(columns={
            "created_at": "Timestamp",
            "temperatura_c": "Temperatura_C",
            "umidade_perc": "Umidade_Pct",
            "nh3_ppm": "Ammonia_NH3_ppm",
            "co2_ppm": "CO2_ppm",
            "poeira_pm25": "Poeira_PM25_ugm3"
        })
        
        stream = io.StringIO()
        df.to_csv(stream, index=False)
        
        response = StreamingResponse(iter([stream.getvalue()]), media_type="text/csv")
        response.headers["Content-Disposition"] = f"attachment; filename=relatorio_auditoria_galpao_{galpao_id[:8]}.csv"
        return response
        
    except Exception as e:
        print(f"Erro em /exportar: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/manutencao/{galpao_id}")
def calcular_manutencao_equipamento(galpao_id: str):
    try:
        resp = supabase.table("telemetria_avicola") \
            .select("nh3_ppm, poeira_pm25") \
            .eq("galpao_id", galpao_id) \
            .execute()
        
        if not resp.data:
            return {
                "galpao_id": galpao_id,
                "horas_totais_operacao": 0.0,
                "limite_vida_util_horas": 200.0,
                "percentual_desgaste": 0.0,
                "status_manutencao": "Saudável"
            }
        
        df = pd.DataFrame(resp.data)
        df['exaustor_ligado'] = (df['nh3_ppm'] >= 20.0) | (df['poeira_pm25'] > 60.0)
        
        # Cada registro simulado equivale a um intervalo de amostragem (ex: 5 segundos)
        horas_exaustor = (df['exaustor_ligado'].sum() * 5) / 3600.0
        
        limite_vida = 200.0 # Limite de vida útil em horas para o motor de exaustão
        percentual = min(round((horas_exaustor / limite_vida) * 100, 1), 100.0)
        
        if percentual >= 80.0:
            status = "Manutenção Recomendada"
        elif percentual >= 50.0:
            status = "Atenção (Desgaste Moderado)"
        else:
            status = "Saudável"
            
        return {
            "galpao_id": galpao_id,
            "horas_totais_operacao": round(horas_exaustor, 2),
            "limite_vida_util_horas": limite_vida,
            "percentual_desgaste": percentual,
            "status_manutencao": status
        }
    except Exception as e:
        print(f"Erro em /manutencao: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class ComandoRequest(BaseModel):
    forcar_exaustor: bool

@app.post("/api/v1/comando/{galpao_id}")
def enviar_comando_borda(galpao_id: str, payload: ComandoRequest):
    estado = "FORÇADO LIGADO (Manual)" if payload.forcar_exaustor else "AUTOMÁTICO (Borda)"
    return {"galpao_id": galpao_id, "modo_operacao": estado, "sucesso": True}

@app.get("/api/v1/rendimento/{galpao_id}")
def verificar_rendimento_industrial(galpao_id: str):
    """
    RN03: Indicador de Perda de Rendimento Industrial no pré-abate.
    """
    try:
        dia_ciclo = obter_dias_lote(galpao_id)
        # O risco foca no pré-abate (geralmente após 35 dias em alta densidade)
        if dia_ciclo < 35:
            return {"status": "Seguro", "mensagem": "Lote fora da janela crítica de pré-abate.", "alerta": False}
            
        # Analisa a flutuação térmica das últimas 24 horas
        ontem = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        resp = supabase.table("telemetria_avicola").select("temperatura_c").eq("galpao_id", galpao_id).gte("created_at", ontem).execute()
        
        if not resp.data: return {"status": "Sem Dados", "alerta": False}
        
        temps = [r['temperatura_c'] for r in resp.data if r['temperatura_c'] is not None]
        delta_temp = max(temps) - min(temps) if temps else 0
        
        if delta_temp >= 8.0:
            return {
                "status": "Risco Crítico de Rendimento",
                "mensagem": f"Flutuação térmica severa ({delta_temp:.1f}°C). Risco de alcalose, hipertermia e carne pálida.",
                "alerta": True
            }
        return {"status": "Estável", "mensagem": f"Flutuação térmica controlada ({delta_temp:.1f}°C).", "alerta": False}
    except Exception as e:
        print(f"Erro em /rendimento capturado: {e}")
        # Retorna um fallback amigável ao invés do Erro 500
        return {
            "status": "Indisponível", 
            "mensagem": "Indicador de rendimento temporariamente indisponível.", 
            "alerta": False
        }

@app.get("/api/v1/zootecnico/{galpao_id}")
def painel_zootecnico(galpao_id: str):
    """
    RN02: Validação do resfriamento cruzando mortalidade e FCA.
    Consumindo registros diários consolidados do Supabase.
    """
    try:
        resp = supabase.table("registros_diarios_zootecnicos") \
            .select("dia_ciclo, mortalidade_cabecas, temperatura_max_c") \
            .eq("galpao_id", galpao_id) \
            .order("dia_ciclo", desc=False) \
            .limit(15) \
            .execute()
        
        # Tratamento para quando não há dados de mortalidade (ex: Lote recém alojado ou Galpão 1 vazio)
        if not resp.data:
            return {
                "galpao_id": galpao_id,
                "fca_atual_protegido": 0.0,
                "grafico_mortalidade": {
                    "labels": [],
                    "mortalidade_cabecas": [],
                    "temperatura_max_c": []
                }
            }

        # Extrai os dados do JSON retornado pelo Supabase
        dias = [f"Dia {row['dia_ciclo']}" for row in resp.data]
        mortalidade = [row['mortalidade_cabecas'] for row in resp.data]
        temperatura_max = [row['temperatura_max_c'] for row in resp.data]
        
        # Simula uma conversão alimentar baseada no dia do ciclo (aves mais velhas convertem pior)
        ultimo_dia = resp.data[-1]['dia_ciclo']
        fca_dinamico = round(1.20 + (ultimo_dia * 0.015), 2)

        return {
            "galpao_id": galpao_id,
            "fca_atual_protegido": fca_dinamico,
            "grafico_mortalidade": {
                "labels": dias,
                "mortalidade_cabecas": mortalidade,
                "temperatura_max_c": temperatura_max
            }
        }
    except Exception as e:
        print(f"Erro detalhado em /zootecnico: {e}")
        # Retorna fallback vazio para não quebrar a tela
        return {
            "galpao_id": galpao_id,
            "fca_atual_protegido": 0.0,
            "grafico_mortalidade": {"labels": [], "mortalidade_cabecas": [], "temperatura_max_c": []}
        }