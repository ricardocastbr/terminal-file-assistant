import json
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

client = OpenAI(
    base_url=os.environ.get("URL"),
    api_key=os.environ.get("API_KEY"),
)

MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"
ROOT = Path(__file__).resolve().parent
SYSTEM_PROMPT = """Você é um assistente de arquivos. Use as ferramentas disponíveis quando o usuário
pedir para listar, ler, criar, editar, copiar, mover, renomear ou excluir arquivos e
pastas. Sempre use caminhos relativos à pasta de trabalho. Explique o que pretende
fazer; a aplicação pedirá confirmação antes de qualquer operação que altere arquivos.
Não diga que uma alteração foi feita até receber o resultado da ferramenta.
"""

def file_tool(name, description, **properties):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }


TEXT = {"type": "string"}
TOOLS = [
    file_tool("list_directory", "Lista arquivos e pastas dentro da pasta de trabalho.", path=TEXT),
    file_tool("create_directory", "Cria uma pasta após confirmação do usuário.", path=TEXT),
    file_tool("read_file", "Lê um arquivo de texto.", path=TEXT),
    file_tool("write_file", "Cria um arquivo ou substitui seu conteúdo após confirmação.", path=TEXT, content=TEXT),
    file_tool("copy_path", "Copia um arquivo ou pasta.", source=TEXT, destination=TEXT),
    file_tool("move_path", "Move ou renomeia um arquivo ou pasta.", source=TEXT, destination=TEXT),
    file_tool("delete_path", "Exclui um arquivo ou pasta após confirmação explícita.", path=TEXT),
]


def safe_path(raw_path, must_exist=False):
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise ValueError("Use caminhos relativos à pasta de trabalho.")

    current = ROOT
    for part in candidate.parts:
        if part in {"", "."}:
            continue
        current = current.parent if part == ".." else current / part
        if current.is_symlink():
            raise ValueError("Acesso negado a caminhos que passam por links simbólicos.")

    resolved = (ROOT / candidate).resolve(strict=must_exist)
    try:
        resolved.relative_to(ROOT)
    except ValueError:
        raise ValueError("Acesso negado: o caminho precisa ficar dentro da pasta app/.")

    if any(part.lower() == ".env" for part in resolved.relative_to(ROOT).parts):
        raise ValueError("Acesso negado ao arquivo .env.")
    return resolved


def confirm_change(action, paths):
    print("\nOperação solicitada: {}".format(action))
    for label, path in paths:
        print("  {}: {}".format(label, path.relative_to(ROOT)))
    answer = input("Confirma? [s/N]: ").strip().lower()
    return answer in {"s", "sim"}


def list_directory(path):
    directory = safe_path(path, must_exist=True)
    if not directory.is_dir():
        raise ValueError("O caminho informado não é uma pasta.")
    entries = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        kind = "pasta" if entry.is_dir() else "arquivo"
        entries.append("{} ({})".format(entry.relative_to(ROOT), kind))
    return "\n".join(entries) if entries else "A pasta está vazia."


def read_file(path):
    file_path = safe_path(path, must_exist=True)
    if not file_path.is_file():
        raise ValueError("O caminho informado não é um arquivo.")
    return file_path.read_text(encoding="utf-8")


def create_directory(path):
    directory = safe_path(path)
    if directory == ROOT:
        raise ValueError("A pasta de trabalho já existe.")
    if directory.exists():
        raise ValueError("O destino já existe.")
    if not confirm_change("criar pasta", [("Destino", directory)]):
        return "Operação cancelada pelo usuário."
    directory.mkdir(parents=True)
    return "Pasta criada: {}".format(directory.relative_to(ROOT))


def write_file(path, content):
    file_path = safe_path(path)
    if file_path == ROOT:
        raise ValueError("Informe o caminho de um arquivo, não a raiz da pasta.")
    if file_path.exists() and not file_path.is_file():
        raise ValueError("O destino existe e não é um arquivo.")
    if not confirm_change("criar arquivo" if not file_path.exists() else "substituir conteúdo", [("Destino", file_path)]):
        return "Operação cancelada pelo usuário."
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return "Arquivo salvo: {}".format(file_path.relative_to(ROOT))


def transfer_path(source, destination, move=False):
    source_path = safe_path(source, must_exist=True)
    destination_path = safe_path(destination)
    if source_path == ROOT:
        action = "mover" if move else "copiar"
        raise ValueError("Não é permitido {} a pasta raiz inteira.".format(action))
    if destination_path == source_path or source_path in destination_path.parents:
        raise ValueError("O destino não pode ser o próprio item nem ficar dentro dele.")
    if destination_path.exists():
        raise ValueError("O destino já existe; escolha outro nome ou caminho.")
    action = "mover/renomear" if move else "copiar"
    if not confirm_change(action, [("Origem", source_path), ("Destino", destination_path)]):
        return "Operação cancelada pelo usuário."
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(source_path), str(destination_path))
    elif source_path.is_dir():
        shutil.copytree(str(source_path), str(destination_path), symlinks=True)
    else:
        shutil.copy2(str(source_path), str(destination_path))
    result = "Item movido" if move else "Cópia criada"
    return "{}: {}".format(result, destination_path.relative_to(ROOT))


def copy_path(source, destination):
    return transfer_path(source, destination)


def move_path(source, destination):
    return transfer_path(source, destination, move=True)


def delete_path(path):
    target = safe_path(path, must_exist=True)
    if target == ROOT:
        raise ValueError("Não é permitido excluir a pasta raiz.")
    if not confirm_change("EXCLUIR permanentemente", [("Alvo", target)]):
        return "Operação cancelada pelo usuário."
    if target.is_dir():
        shutil.rmtree(str(target))
    else:
        target.unlink()
    return "Item excluído: {}".format(target.relative_to(ROOT))


FUNCTIONS = {
    "list_directory": list_directory,
    "create_directory": create_directory,
    "read_file": read_file,
    "write_file": write_file,
    "copy_path": copy_path,
    "move_path": move_path,
    "delete_path": delete_path,
}


def call_function(name, arguments):
    function = FUNCTIONS.get(name)
    if function is None:
        return "Ferramenta desconhecida: {}".format(name)
    try:
        return function(**arguments)
    except (OSError, ValueError, TypeError) as error:
        return "Não foi possível concluir a operação: {}".format(error)


def send_message(history, message):
    history.append({"role": "user", "content": message})
    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=history,
            tools=TOOLS,
            tool_choice="auto",
            parallel_tool_calls=False,
            temperature=1,
            top_p=0.95,
        )
        assistant_message = response.choices[0].message
        assistant_entry = {"role": "assistant"}
        if assistant_message.content is not None:
            assistant_entry["content"] = assistant_message.content
        if assistant_message.tool_calls:
            assistant_entry["tool_calls"] = [call.model_dump(exclude_none=True) for call in assistant_message.tool_calls]
        history.append(assistant_entry)

        if not assistant_message.tool_calls:
            return assistant_message.content or ""

        for tool_call in assistant_message.tool_calls:
            try:
                arguments = json.loads(tool_call.function.arguments or "{}")
                result = call_function(tool_call.function.name, arguments)
            except (json.JSONDecodeError, TypeError) as error:
                result = "Argumentos inválidos para a ferramenta: {}".format(error)
            history.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(result),
            })


def main():
    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    print("Assistente de arquivos ativo. Acesso limitado a app/. Digite 'sair' para encerrar.")
    while True:
        try:
            mensagem = input("Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nChat encerrado.")
            break

        if mensagem.lower() in {"sair", "exit", "quit"}:
            print("Chat encerrado.")
            break
        if not mensagem:
            continue

        resposta = send_message(history, mensagem)
        print("\033[38;2;255;165;0mBot: {}\033[0m".format(resposta))


if __name__ == "__main__":
    main()
