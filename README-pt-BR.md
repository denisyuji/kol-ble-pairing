# kol-ble-pairing — pareamento de KOL Smart Badges por Bluetooth

[English](README.md) | Português

Configura KOL Smart Badges (firmware de fábrica) em uma linha de produção via BLE (Bluetooth Low Energy).

## Download

Baixe o arquivo para o seu computador na [última versão](https://github.com/denisyuji/kol-ble-pairing/releases/latest):

| Computador | Arquivo |
|---|---|
| Linux (Ubuntu 20.04 ou mais novo) | `kol-ble-pairing-<versão>-linux-x64.tar.gz` |
| Mac com Apple Silicon (M1 ou mais novo) | `kol-ble-pairing-<versão>-macos-arm64.tar.gz` |
| Windows 10 (1709 ou mais novo) ou 11 | `kol-ble-pairing-<versão>-windows-x64.exe` |

Não é preciso instalar Python nem outro programa. Os comandos abaixo usam a versão 1.2.0: troque pela versão que você baixou.

## Instalar e executar

**Linux** (as distribuições desktop já vêm com suporte a Bluetooth, o BlueZ):

```sh
tar -xzf kol-ble-pairing-1.2.0-linux-x64.tar.gz
bluetoothctl power on
./kol-ble-pairing
```

**macOS**

```sh
tar -xzf kol-ble-pairing-1.2.0-macos-arm64.tar.gz
./kol-ble-pairing
```

Se o macOS se recusar a abrir o arquivo ("não pode ser aberto" ou "desenvolvedor não identificado"), execute `xattr -d com.apple.quarantine kol-ble-pairing` e tente de novo. Se o `xattr` responder `No such xattr`, o arquivo não estava bloqueado e não é preciso fazer nada.

Na primeira execução, o macOS pede acesso ao Bluetooth para o Terminal: permita (Ajustes do Sistema → Privacidade e Segurança → Bluetooth).

**Windows**: abra o PowerShell na pasta do download e execute:

```powershell
.\kol-ble-pairing-1.2.0-windows-x64.exe
```

Se o SmartScreen ou o antivírus bloquear, permita o arquivo. Não é preciso parear o badge nas configurações de Bluetooth do Windows.

## Pareando badges

1. Inicie a ferramenta. Ela pede o nome e a senha da rede Wi-Fi, uma única vez para toda a sessão.
2. Coloque um badge em **modo de pareamento**. A ferramenta o encontra, mostra o número de série (SN, ex.: `4S1006EC4284510011`, o ID nas etiquetas do badge e da caixa), configura e mostra uma linha por etapa.
3. Cada badge termina com uma destas linhas, em verde ou vermelho:
   - `==== SUCCESS: SMCD510011 is paired and connected to '<rede>' ====`: pronto, pegue o próximo badge.
   - `!!!! FAILED: SMCD510011: <motivo>`: configure esse badge de novo ou confira as configurações dele manualmente. Se o badge não enxerga a rede, a ferramenta lista as redes que ele vê.

   Depois de cada badge, a ferramenta lista todos os badges processados até o momento, pelo SN, com `paired`, `VERIFY` ou `FAILED`, para você conferir com as etiquetas.
4. Pressione **Ctrl-C** para encerrar. A ferramenta mostra a mesma lista para a sessão inteira.

Vários badges podem estar em modo de pareamento ao mesmo tempo: a ferramenta os pareia um de cada vez, começando pelo sinal mais forte. Badges já pareados na sessão são ignorados. Um pareamento leva em geral cerca de 20 s.

### Opções

| Opção | Significado |
|---|---|
| `--wifi-file ARQUIVO` | Lê as credenciais do Wi-Fi de um arquivo, para não ter que digitá-las (veja abaixo). |
| `--ssid NOME`, `--password SENHA` | Passa as credenciais na linha de comando, entre aspas simples: `--password 'minha!senha'`. Sem aspas, o shell altera caracteres como `!` e `$` (no macOS, `!!` insere o comando anterior). A senha também fica no histórico do shell, então prefira `--wifi-file` ou digitá-la quando a ferramenta pedir. |
| `--name SMCD510011` | Pareia apenas o badge com esse nome. |
| `--once` | Pareia um badge e sai. Se houver vários em modo de pareamento, para e pede `--name`. |
| `--help` | Lista as opções e mostra a versão. |
| `--version` | Mostra a versão. |

Um arquivo de Wi-Fi é um arquivo de texto como este (mantenha-o privado, por exemplo com `chmod 600`):

```text
ssid=Factory WiFi
password=a senha
```

A ferramenta também lê as credenciais das variáveis de ambiente `KOL_WIFI_SSID` e `KOL_WIFI_PASSWORD`.

### O que é configurado no badge

| Configuração | Valor |
|---|---|
| Servidor da API (config, record, log) | `badgeapi.yourkol.ai` |
| Duração do segmento | 600 s |
| Fuso horário | GMT-3 |
| Wi-Fi | a rede informada |

As demais configurações do badge ficam como estavam. "Conectado" significa que o badge entrou no Wi-Fi e obteve um endereço IP. A ferramenta não consegue verificar por Bluetooth se o badge alcança o servidor da API.

---

## Para desenvolvedores

### O que a ferramenta faz

1. Espera um badge anunciando como `SCMD…`/`SMCD…` com nome de 10 caracteres (ex.: `SMCD510011`). Nomes com outro tamanho são modelos que usam outro conjunto de comandos, então ela os ignora.
2. Abre uma sessão BluFi (DH + AES-128-CFB + CRC16).
3. Lê a configuração, grava os valores fixos com o comando do fabricante `02 12` (`BlufiMsg.NVS`, codificado como o app do fabricante faz: campos de texto sempre enviados, no máximo 255 bytes cada e 1021 no total) e lê de volta para conferir.
4. Envia o SSID e a senha e pede ao badge que conecte. Para assim que o badge informa um código de falha (senha errada, rede não encontrada, falha de associação) e só então pede ao badge que procure as redes.

Os valores fixos são constantes no início de `kol_ble_pairing.py`. O badge recebe uma string TZ POSIX com o sinal invertido, então GMT-3 é enviado como `UTC+3`. Referência do protocolo: `AGENTS.md` aqui, e `docs/BLE_PROVISIONING.md` e `docs/blufi.proto` no workspace ESP32.

Códigos de saída com `--once`: `0` pareado e conectado, `1` erro, `2` conectado, mas a configuração lida de volta difere da enviada, `130` interrompido. No modo linha de produção: `130` com Ctrl-C, `1` em erro do adaptador Bluetooth.

### Executando a partir do código-fonte

Requer Python 3.10 ou mais novo (3.11+ para o Ctrl-C funcionar bem no Windows):

```sh
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python kol_ble_pairing.py --once --ssid "MinhaRede"
```

### Gerando os executáveis

O PyInstaller não faz compilação cruzada, então cada sistema gera o próprio executável em `dist/`:

| Sistema | Comando |
|---|---|
| Linux | `./build-container.sh`: gera com podman no Debian bullseye (glibc 2.31), então o binário roda no Ubuntu 20.04 e mais novos. Um binário gerado com `./build.sh` direto só roda em distribuições pelo menos tão novas quanto a máquina de build. |
| macOS | `./build.sh` |
| Windows | `powershell -ExecutionPolicy Bypass -File build.ps1` |

### CI e versões

`.github/workflows/build.yml` gera os três a cada push na `main`, em pull requests e sob demanda. Os executáveis ficam em **Artifacts** da execução. Enviar uma tag como `v1.0.1` também os publica como uma versão (release) no GitHub. Antes, ajuste `VERSION` em `kol_ble_pairing.py` para o mesmo número: o CI falha com uma tag diferente.

`./fetch-artifacts.sh` envia o branch atual, espera a execução do CI e baixa os três executáveis em `artifacts/`. Requer o `gh` autenticado e se recusa a rodar com alterações não commitadas. Às vezes o job do macOS fica esperando um runner até o GitHub cancelá-lo. Execute-o de novo com `gh run rerun <run-id> --failed`.
