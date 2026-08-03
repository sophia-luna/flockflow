import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

def get_db_client() -> Client:
    """
    Inicializa e retorna a instância do cliente oficial do Supabase.
    """
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise ValueError("As credenciais SUPABASE_URL e SUPABASE_ANON_KEY não foram encontradas no arquivo .env.")
    
    supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    
    return supabase_client