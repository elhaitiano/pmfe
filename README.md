# PMFE

**Plataforma de Monitorização de Formação EPAL**, no âmbito do projecto PROÁGUA.

A PMFE reúne o cadastro de técnicos, o planeamento de formações, as inscrições, as presenças, os resultados e a entrega de certificados. Permite acompanhar o percurso formativo de cada técnico e produzir indicadores e relatórios para apoiar a gestão.

A aplicação funciona localmente num único computador, através do navegador, com interface em português. Após a instalação das dependências, as funcionalidades principais estão disponíveis sem ligação à Internet.

## Funcionalidades

| Módulo | O que permite fazer |
| --- | --- |
| Cadastro de técnicos | Pesquisar por nome ou número mecanográfico, criar e corrigir fichas, consultar direcção, categoria e contactos. |
| Histórico de formação | Consultar e actualizar participações, duração, presenças, notas, resultados e entrega de certificados. |
| Ficha individual | Consultar dados e indicadores do técnico, visualizar o histórico e imprimir ou guardar a ficha em PDF pelo navegador. |
| Catálogo de formações | Criar acções com categoria, objectivos e duração prevista; editar ou arquivar acções existentes. |
| Planeamento e turmas | Organizar grupos G1, G2… por acção, com capacidade, local, formador, horários e calendário de dias efectivos, incluindo dias não consecutivos. |
| Inscrições | Seleccionar participantes, rever avisos de histórico, justificar repetições e transferir inscrições entre grupos ainda não iniciados. |
| Actualização colectiva | Registar presenças verificadas, notas, resultados e certificados de uma mesma edição, com revisão antes da confirmação. |
| Listas de presença | Gerar listas diárias em PDF ou Excel segundo o modelo institucional, com espaços para assinatura. |
| Dashboard executivo | Consultar indicadores e gráficos, filtrar por período, direcção, área e situação, e explorar os registos correspondentes. |
| Relatórios e arquivo | Pré-visualizar, gerar, arquivar e descarregar relatórios PDF por período ou direcção. |
| Assistente EPAL | Pesquisar orientações locais, seguir passos e utilizar atalhos. Os administradores podem editar e publicar os guias. |
| Certificados e documentos privados | Distinguir emissão, declaração e entregas ao participante/RH; anexar CV, certificados e comprovativos PDF até 10 MB, com acesso administrativo. |
| Cópias de segurança e auditoria | Exportar a base, os relatórios e os documentos privados, conservar cópias antes das migrações e consultar o registo das operações administrativas. |

A eliminação de turmas preparatórias e de acções criadas por engano exige acesso administrativo, motivo e confirmação `ELIMINAR`. Turmas com histórico e acções com grupos ou histórico associado ficam protegidas contra eliminação.

## Percurso de utilização

1. Criar ou seleccionar uma acção no catálogo.
2. Criar um grupo e definir dias, horário, capacidade, local e formador.
3. Seleccionar os técnicos, rever os avisos e confirmar as inscrições.
4. Gerar a lista de presença de cada dia em PDF ou Excel.
5. Iniciar o grupo para criar as participações no histórico.
6. Actualizar presenças verificadas, notas, resultados e entregas de certificados.
7. Consultar indicadores, produzir relatórios e guardar cópias de segurança.

## Regras de tratamento dos dados

- Uma inscrição não confirma presença nem formação realizada.
- **Horas previstas:** dias de formação × horas por dia.
- **Horas realizadas:** dias de presença × horas por dia.
- **Assiduidade individual:** presenças ÷ dias previstos × 100.
- **Assiduidade consolidada:** soma das presenças ÷ soma dos dias elegíveis × 100.
- Uma nota zero é válida e distingue-se de uma nota não preenchida.
- O resultado da formação e a entrega do certificado são informações independentes.
- Zero presenças sem verificação não confirma uma ausência.
- Um PDF arquivado conserva os dados do momento da geração; alterações posteriores não reescrevem o documento.

## Tecnologias e arquitectura

| Componente | Tecnologia |
| --- | --- |
| Backend | Python 3.12, Django e Waitress |
| Interface | Templates Django, HTML, CSS/Tailwind já compilado e JavaScript |
| Serviço frontend | Nginx para ficheiros estáticos e encaminhamento de pedidos para Django |
| Base de dados | SQLite num volume Docker persistente |
| Documentos | ReportLab para PDF, openpyxl para importação Excel e modelo XLSX para listas de presença |
| Execução | Docker Compose |

```text
Navegador — http://localhost:8080
    │
    ▼
frontend: Nginx
    ├── CSS, JavaScript, imagens e fontes locais
    └── backend: Django + Waitress
                    └── volume database
                         ├── pmf.sqlite3
                         ├── secret.key
                         ├── reports/
                         ├── documents/ (PDFs privados)
                         └── backups/
```

Existem **dois serviços Docker**, `frontend` e `backend`. `database` é um volume persistente, não um terceiro contentor. Django gera as páginas HTML no servidor.

## Instalação

### Requisitos

- **Linux:** Docker Engine e Docker Compose instalados e em execução.
- **Windows:** Docker Desktop configurado para contentores Linux.
- Ligação à Internet na primeira construção, caso as imagens e dependências ainda não estejam disponíveis localmente.

Não é necessário instalar Python, Node.js ou Microsoft Excel no computador anfitrião para utilizar a aplicação com Docker.

### Primeiro arranque

Descarregue ou clone o repositório **pmfe**. A partir da pasta que contém `compose.yaml`, execute:

```bash
docker compose config --quiet
docker compose build
docker compose up -d --wait
docker compose exec backend python manage.py createsuperuser
```

Abra **http://localhost:8080** e entre com a conta criada.

Um volume novo começa com uma base vazia. Os dados pessoais, as contas e as cópias de segurança não são distribuídos com o repositório. As migrações são aplicadas automaticamente no arranque do backend.

### Configuração local

O repositório chama-se `pmfe`. O ficheiro Compose define `pmf-containers` como nome de projecto por defeito. Para uma **nova instalação**, pode definir outro nome num ficheiro `.env`, junto de `compose.yaml`:

```dotenv
COMPOSE_PROJECT_NAME=pmfe
PMF_PORT=8080
```

**Numa instalação existente, mantenha o nome de projecto Compose e o ficheiro `.env` utilizados anteriormente.** Mudar o nome do projecto selecciona outro volume e pode apresentar uma base vazia. O exemplo acima não deve substituir a configuração de uma instalação que já contém dados.

Se a porta 8080 estiver ocupada, verifique se existe outra instância da aplicação em execução. Para escolher outra porta, defina `PMF_PORT=8081` no `.env`, execute `docker compose up -d --wait` e aceda a http://localhost:8081.

O serviço é publicado apenas em `127.0.0.1`, para acesso no próprio computador.

## Contas e permissões

- Utilizadores autenticados podem consultar dados e documentos de acordo com as permissões das páginas.
- Administradores podem alterar dados, gerir turmas, publicar guias, efectuar eliminações autorizadas e criar cópias de segurança.
- A conta criada com `createsuperuser` tem acesso administrativo. Não é necessário recriá-la em cada actualização quando a mesma base é conservada.
- As permissões são verificadas no servidor.

## Importação de dados Excel

A importação utiliza o comando Django `import_excel`. O ficheiro deve respeitar uma das estruturas EPAL/PROÁGUA reconhecidas pelo importador.

Coloque o ficheiro na pasta do projecto com o nome `dados.xlsx` e copie-o para o backend:

```bash
docker compose cp dados.xlsx backend:/tmp/import.xlsx
```

Execute primeiro uma **simulação sem guardar os registos**:

```bash
docker compose exec backend python manage.py import_excel /tmp/import.xlsx --report /tmp/simulacao.json
docker compose exec backend python -m json.tool /tmp/simulacao.json
```

Reveja as alterações e os erros indicados. Crie uma cópia de segurança antes da importação efectiva, sobretudo se a base já contém dados. Para guardar os registos válidos:

```bash
docker compose exec backend python manage.py import_excel /tmp/import.xlsx --apply --report /data/resultado-importacao.json
```

Na importação integrada, o número mecanográfico identifica o técnico e o ID de participação permite reconhecer participações existentes. A reimportação conserva os resultados das participações integradas já guardadas. Dados provenientes de outro formato ou sistema de identificação exigem revisão antes da fusão.

Os caminhos `/tmp` e `/data` destes comandos pertencem ao contentor. Os ficheiros Excel de origem e os relatórios com dados pessoais devem permanecer fora do repositório público.

## Certificados e documentos privados

No histórico, os administradores encontram **Certificado e entregas** em cada participação. A emissão, a declaração do responsável, a entrega confirmada ao participante e a entrega confirmada aos RH têm campos separados. Declarações conservam origem e responsável, sem inventar datas; não aumentam o indicador de entregas confirmadas. A entrega confirmada exige a data efectiva.

O acesso **Documentos privados**, na ficha ou histórico, permite anexar CV ao trabalhador e certificados/comprovativos à participação correcta. Apenas administradores podem carregar, consultar e descarregar os PDFs. Os ficheiros ficam fora dos recursos públicos, com nomes internos UUID e verificação SHA-256. O controlo do formato é básico; o conteúdo não é convertido nem executado pelo servidor.

Arquivar preserva o ficheiro e o histórico; reactivar exige um motivo, tal como arquivar. Anexar um comprovativo não altera automaticamente os estados do certificado. Participações com documentos associados ficam protegidas contra eliminação.

## Cópias de segurança e persistência

A base fica em `/data/pmf.sqlite3` dentro do backend. O volume `database` conserva os dados quando os contentores são substituídos ou removidos sem apagar o volume.

Para exportar uma cópia, entre como administrador e escolha **Cópia de segurança**:

- Sem relatórios nem documentos privados, a aplicação fornece um ficheiro SQLite.
- Com relatórios ou documentos privados, fornece um ZIP com a base, os PDFs (incluindo documentos arquivados) e a chave local quando disponível. Conserve o ZIP completo. A exportação recusa documentos privados ausentes ou alterados.

No arranque com uma base existente, o script Docker guarda uma cópia da base, dos relatórios, dos documentos privados e da chave em `/data/backups/` antes das migrações. Estas cópias acumulam-se no mesmo volume; mantenha também uma cópia noutro suporte para protecção contra perda do disco.

Para recuperar um ZIP ou SQLite exportado pela aplicação, execute no ambiente Python do backend:

```bash
python manage.py restore_backup COPIA.zip --destination NOVA_PASTA
```

A cópia tem de estar acessível nesse ambiente; em Docker, disponibilize-a num volume ou copie-a para o contentor. A pasta principal do destino deve existir, ser gravável pelo backend, e `NOVA_PASTA` não pode existir. A recuperação verifica a integridade SQLite, as relações e as assinaturas dos PDFs; não substitui uma base em uso. O limite é 5 GB por arquivo e 1 GB por ficheiro.

Depois da validação, pare a aplicação, conserve a instalação anterior e configure `PMF_DATA_DIR`/o volume para utilizar a pasta recuperada. Os ficheiros devem continuar acessíveis ao utilizador do backend (`10001:10001`). Não elimine a cópia anterior antes de testar o arranque. Uma exportação SQL exige uma ferramenta SQLite; este comando aceita SQLite binário ou ZIP da aplicação.

**Não utilize `docker compose down -v` numa actualização normal: essa opção remove o volume de dados do projecto.**

## Actualização

Crie uma cópia de segurança, actualize o código e execute na pasta do projecto:

```bash
docker compose build
docker compose up -d --wait
```

Não é necessário executar `down` antes da construção. O comando `up` substitui os contentores que precisam de actualização. Mantenha o mesmo `.env` e o mesmo nome de projecto Compose para conservar a ligação à base existente.

Depois do arranque, actualize o navegador com **Ctrl+F5**. Não volte a importar o Excel para aplicar uma actualização de código: as migrações adaptam a base existente.

## Comandos úteis

```bash
# Consultar o estado dos contentores
docker compose ps

# Consultar as últimas mensagens dos serviços
docker compose logs --tail=100 backend frontend

# Parar e voltar a iniciar os mesmos contentores
docker compose stop
docker compose start

# Remover os contentores, conservando o volume de dados
docker compose down

# Abrir a consola Django
docker compose exec backend python manage.py shell
```

## Testes

Para executar os testes com as dependências da imagem backend, sem utilizar a base activa:

```bash
docker compose run --rm --no-deps -e PMF_DATA_DIR=/tmp/pmf-tests backend python manage.py test --noinput
```

Os testes abrangem regras de dados, permissões, revisão colectiva, planeamento, exportações, relatórios, assistente e eliminações protegidas. O teste opcional de leitura estrutural de PDF é ignorado quando `pypdf` não está disponível.

## Estrutura do repositório

```text
pmfe/
├── app/
│   ├── pmf/                 # Configuração Django e rotas
│   ├── training/            # Modelos, lógica, migrações e testes
│   │   └── resources/       # Modelo Excel de listas de presença
│   ├── templates/           # Páginas HTML
│   ├── static/              # CSS, JavaScript, fontes e imagens
│   ├── manage.py
│   └── requirements.txt
├── docker/
│   ├── nginx.conf
│   └── startup.py
├── backend.Dockerfile
├── frontend.Dockerfile
├── compose.yaml
├── .dockerignore
├── .gitignore
└── README.md
```

O `.gitignore` exclui ambientes locais, `.env`, segredos, bases, cópias de segurança, exportações SQL e ficheiros Excel com dados pessoais. O modelo `app/training/resources/attendance_template.xlsx` é conservado porque a aplicação depende dele.

Entre os documentos Markdown, apenas o `README.md` da raiz está previsto para publicação. Os restantes ficheiros `.md` ficam excluídos pelo `.gitignore`.

## Limitações actuais

- A configuração destina-se a um único computador e a uma única instância backend com SQLite.
- A avaliação no terreno/360° e o plano de desenvolvimento ainda não estão activos. O arquivo privado de CV já está disponível para administradores.
- O assistente utiliza guias locais; não é uma IA generativa nem aprende automaticamente novas funcionalidades.
- Existem listas diárias de presença, mas o cálculo de horas utiliza os totais de dias de presença registados.
- A plataforma acompanha a entrega de certificados; a emissão automática a partir de um modelo ainda não está implementada.
- Os indicadores dependem da qualidade dos registos. Os dados EPAL não validados pelos RH continuam provisórios.


## Avaliação da formação, provas e reacção

O menu **Avaliação**, disponível aos administradores, permite pesquisar uma participação existente e registar:

- **Avaliação da formação:** logística numa escala explícita de 1 a 5, nota final de 0 a 20 e resultado final independentes. Valores desconhecidos permanecem vazios; concluir a formação não atribui aprovação.
- **Reacção:** organização, conteúdo, formador e utilidade de 1 a 5, com comentários. É possível guardar uma resposta parcial, conservando vazios os itens não respondidos.
- **Provas:** tipo diagnóstica/intermédia/final, questões de resposta textual com critérios de correcção manual, referência do manual e cotação positiva. O limiar de aprovação é configurável entre 0% e 100%; o formulário propõe 50% e o responsável deve confirmar o critério aplicável.
- **Tentativas e respostas:** um administrador inicia a primeira tentativa. As seguintes exigem a conclusão da anterior e um motivo de autorização. Todas as tentativas permanecem no histórico.

Para preparar uma prova, guarde o título e as questões. Cada gravação apresenta mais três espaços para novas questões, até ao limite de 100. Após iniciar a primeira tentativa, as questões e o critério ficam fechados; crie uma nova prova para uma versão diferente.

Na correcção, registe as respostas e os pontos atribuídos. Uma pontuação vazia significa **não classificada**; zero é uma classificação explícita. Pode guardar um rascunho. **Concluir correcção** exige todas as pontuações, data, responsável e fonte documental, e conserva o resultado. A percentagem é `pontos obtidos / cotação total × 100`; a nota /20 é a percentagem dividida por cinco. A aprovação compara a percentagem sem arredondamento com o limiar registado; os ecrãs apresentam duas casas decimais.

O resultado de uma tentativa **não altera automaticamente** a nota final da participação. O responsável regista essa decisão em **Avaliação e nota final**, com motivo e fonte. Isto permite distinguir provas diagnósticas, reavaliações e uma classificação final baseada em vários elementos.

Os registos incluem fonte documental, data, responsável e estado **Por confirmar / Validado / Rever**. Marcar como validado exige data, responsável e fonte; não significa que a aplicação tenha verificado o documento. Correcções exigem motivo, conservam o antes/depois na auditoria e rejeitam gravações de uma janela desactualizada. Uma tentativa concluída é conservada; uma nova avaliação exige outra tentativa autorizada.

Os registos são privados aos administradores; não existe portal de realização de provas pelos participantes nem correcção automática de respostas. As relações com a participação evitam duplicar o cadastro do trabalhador. A cópia de segurança e a recuperação da base incluem avaliações, questões, tentativas e respostas. Esta etapa não importa automaticamente folhas Excel nem implementa a avaliação 360°.
