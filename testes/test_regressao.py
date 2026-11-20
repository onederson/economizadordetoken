import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMINHO_GUARDA = os.path.join(RAIZ, "guarda.py")
CAMINHO_INSTALAR = os.path.join(RAIZ, "instalar.py")
CAMINHO_MAESTRO = os.path.join(RAIZ, "maestro.py")


class TestGuarda(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.env = dict(os.environ)
        self.env["HOME"] = self.temp_dir
        self.env["USERPROFILE"] = self.temp_dir
        for k in ("MAESTRO_EU", "MAESTRO_GUARDA", "MAESTRO_LIMITE_AVISO", "MAESTRO_LIMITE_PARADA"):
            self.env.pop(k, None)

        self.patcher = unittest.mock.patch.dict(os.environ, {
            "HOME": self.temp_dir,
            "USERPROFILE": self.temp_dir,
        })
        self.patcher.start()
        for k in ("MAESTRO_EU", "MAESTRO_PROFUNDIDADE", "MAESTRO_GUARDA", "MAESTRO_LIMITE_AVISO", "MAESTRO_LIMITE_PARADA"):
            if k in os.environ:
                del os.environ[k]

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def rodar_guarda(self, modo, entrada=None, env_extra=None):
        env = dict(self.env)
        if env_extra:
            env.update(env_extra)
        inp = entrada if isinstance(entrada, str) else (json.dumps(entrada) if entrada is not None else "")
        cmd = [sys.executable, CAMINHO_GUARDA]
        if modo:
            cmd.append(modo)
        return subprocess.run(
            cmd,
            input=inp,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

    def test_01_prompt_curto_ignorado(self):
        res = self.rodar_guarda("prompt", {"session_id": "s1", "cwd": "C:/x", "prompt": "ok"})
        self.assertEqual(res.stdout.strip(), "")

    def test_02_prompt_longo_recebe_criterio(self):
        prompt_longo = "A" * 45
        res = self.rodar_guarda("prompt", {"session_id": "s1", "cwd": "C:/x", "prompt": prompt_longo})
        data = json.loads(res.stdout)
        self.assertEqual(data.get("hookSpecificOutput", {}).get("hookEventName"), "UserPromptSubmit")
        self.assertIn("maestro:", data.get("hookSpecificOutput", {}).get("additionalContext", ""))

    def test_03_prompt_barra_ignorado(self):
        prompt_barra = "/" + "A" * 45
        res = self.rodar_guarda("prompt", {"session_id": "s1", "cwd": "C:/x", "prompt": prompt_barra})
        self.assertEqual(res.stdout.strip(), "")

    def test_04_aviso_no_limite(self):
        payload = {"session_id": "s2", "cwd": "C:/x", "tool_name": "Edit", "tool_input": {}}
        env_extra = {"MAESTRO_LIMITE_AVISO": "3"}
        res1 = self.rodar_guarda("ferramenta", payload, env_extra=env_extra)
        self.assertEqual(res1.stdout.strip(), "")
        res2 = self.rodar_guarda("ferramenta", payload, env_extra=env_extra)
        self.assertEqual(res2.stdout.strip(), "")
        res3 = self.rodar_guarda("ferramenta", payload, env_extra=env_extra)
        data = json.loads(res3.stdout)
        self.assertEqual(data.get("hookSpecificOutput", {}).get("hookEventName"), "PostToolUse")
        self.assertIn("maestro.py", data.get("hookSpecificOutput", {}).get("additionalContext", ""))

    def test_05_ferramenta_nao_trabalho(self):
        payload = {"session_id": "s_notrabalho", "cwd": "C:/x", "tool_name": "TodoWrite", "tool_input": {}}
        res = self.rodar_guarda("ferramenta", payload, env_extra={"MAESTRO_LIMITE_AVISO": "1"})
        self.assertEqual(res.stdout.strip(), "")

    def test_06_delegacao_zera_contador(self):
        env_extra = {"MAESTRO_LIMITE_AVISO": "3"}
        edit_payload = {"session_id": "s3", "cwd": "C:/x", "tool_name": "Edit", "tool_input": {}}
        bash_payload = {"session_id": "s3", "cwd": "C:/x", "tool_name": "Bash", "tool_input": {"command": "python ~/.maestro/maestro.py run x"}}
        for _ in range(2):
            r = self.rodar_guarda("ferramenta", edit_payload, env_extra=env_extra)
            self.assertEqual(r.stdout.strip(), "")
        r_bash = self.rodar_guarda("ferramenta", bash_payload, env_extra=env_extra)
        self.assertEqual(r_bash.stdout.strip(), "")
        for _ in range(2):
            r = self.rodar_guarda("ferramenta", edit_payload, env_extra=env_extra)
            self.assertEqual(r.stdout.strip(), "")
        estado_path = os.path.join(self.temp_dir, ".maestro", "guarda", "s3.json")
        self.assertTrue(os.path.exists(estado_path))
        with open(estado_path, encoding="utf-8") as f:
            estado = json.load(f)
        self.assertEqual(estado.get("delegacoes"), 1)
        self.assertEqual(estado.get("desde_delegacao"), 2)

    def test_07_subagente_barato(self):
        payload_barato = {"session_id": "s_sub1", "cwd": "C:/x", "tool_name": "Agent", "tool_input": {"subagent_type": "implementador"}}
        self.rodar_guarda("ferramenta", payload_barato)
        estado1 = os.path.join(self.temp_dir, ".maestro", "guarda", "s_sub1.json")
        with open(estado1, encoding="utf-8") as f:
            st1 = json.load(f)
        self.assertEqual(st1.get("delegacoes"), 1)

        payload_caro = {"session_id": "s_sub2", "cwd": "C:/x", "tool_name": "Agent", "tool_input": {"subagent_type": "general-purpose"}}
        self.rodar_guarda("ferramenta", payload_caro)
        estado2 = os.path.join(self.temp_dir, ".maestro", "guarda", "s_sub2.json")
        if os.path.exists(estado2):
            with open(estado2, encoding="utf-8") as f:
                st2 = json.load(f)
            self.assertEqual(st2.get("delegacoes", 0), 0)

    def test_08_skill_nao_e_delegar(self):
        payload_skill = {"session_id": "s_skill", "cwd": "C:/x", "tool_name": "Skill", "tool_input": {"skill": "maestro"}}
        self.rodar_guarda("ferramenta", payload_skill)
        estado_path = os.path.join(self.temp_dir, ".maestro", "guarda", "s_skill.json")
        if os.path.exists(estado_path):
            with open(estado_path, encoding="utf-8") as f:
                st = json.load(f)
            self.assertEqual(st.get("delegacoes", 0), 0)

    def test_09_parada_bloqueia_uma_vez(self):
        env_extra = {"MAESTRO_LIMITE_PARADA": "2"}
        payload_edit = {"session_id": "s5", "cwd": "C:/x", "tool_name": "Edit", "tool_input": {}}
        for _ in range(2):
            self.rodar_guarda("ferramenta", payload_edit, env_extra=env_extra)
        stop_payload = {"session_id": "s5", "cwd": "C:/x", "stop_hook_active": False}
        r1 = self.rodar_guarda("parar", stop_payload, env_extra=env_extra)
        data = json.loads(r1.stdout)
        self.assertEqual(data.get("decision"), "block")
        self.assertIn("Auditoria", data.get("reason", ""))

        r2 = self.rodar_guarda("parar", stop_payload, env_extra=env_extra)
        self.assertEqual(r2.stdout.strip(), "")

        hist_path = os.path.join(self.temp_dir, ".maestro", "historico.jsonl")
        self.assertTrue(os.path.exists(hist_path))
        with open(hist_path, encoding="utf-8") as f:
            linhas = [json.loads(line) for line in f if line.strip()]
        self.assertTrue(any(l.get("evento") == "auditoria" for l in linhas))

    def test_10_parada_stop_hook_active(self):
        env_extra = {"MAESTRO_LIMITE_PARADA": "2"}
        payload_edit = {"session_id": "s_stop_act", "cwd": "C:/x", "tool_name": "Edit", "tool_input": {}}
        for _ in range(2):
            self.rodar_guarda("ferramenta", payload_edit, env_extra=env_extra)
        stop_payload = {"session_id": "s_stop_act", "cwd": "C:/x", "stop_hook_active": True}
        res = self.rodar_guarda("parar", stop_payload, env_extra=env_extra)
        self.assertEqual(res.stdout.strip(), "")

    def test_11_parada_nao_bloqueia_quem_delegou(self):
        env_extra = {"MAESTRO_LIMITE_PARADA": "2"}
        bash_payload = {"session_id": "s_del", "cwd": "C:/x", "tool_name": "Bash", "tool_input": {"command": "python ~/.maestro/maestro.py run x"}}
        self.rodar_guarda("ferramenta", bash_payload, env_extra=env_extra)
        edit_payload = {"session_id": "s_del", "cwd": "C:/x", "tool_name": "Edit", "tool_input": {}}
        for _ in range(3):
            self.rodar_guarda("ferramenta", edit_payload, env_extra=env_extra)
        stop_payload = {"session_id": "s_del", "cwd": "C:/x", "stop_hook_active": False}
        res = self.rodar_guarda("parar", stop_payload, env_extra=env_extra)
        self.assertEqual(res.stdout.strip(), "")

    def test_12_executor_ignorado_por_variavel(self):
        env_extra = {"MAESTRO_EU": "cc-haiku", "MAESTRO_LIMITE_AVISO": "1"}
        prompt_payload = {"session_id": "s_ex_var", "cwd": "C:/x", "prompt": "A" * 45}
        r_prompt = self.rodar_guarda("prompt", prompt_payload, env_extra=env_extra)
        self.assertEqual(r_prompt.stdout.strip(), "")
        edit_payload = {"session_id": "s_ex_var", "cwd": "C:/x", "tool_name": "Edit", "tool_input": {}}
        for _ in range(30):
            r_edit = self.rodar_guarda("ferramenta", edit_payload, env_extra=env_extra)
            self.assertEqual(r_edit.stdout.strip(), "")

    def test_13_executor_ignorado_por_pasta(self):
        wt_dir = os.path.join(self.temp_dir, ".maestro", "wt", "job1")
        prompt_payload = {"session_id": "s_wt", "cwd": wt_dir, "prompt": "A" * 45}
        res = self.rodar_guarda("prompt", prompt_payload)
        self.assertEqual(res.stdout.strip(), "")

    def test_14_guarda_desligado(self):
        env_extra = {"MAESTRO_GUARDA": "0"}
        prompt_payload = {"session_id": "s_off", "cwd": "C:/x", "prompt": "A" * 45}
        res = self.rodar_guarda("prompt", prompt_payload, env_extra=env_extra)
        self.assertEqual(res.stdout.strip(), "")

    def test_15_entrada_invalida_nunca_quebra(self):
        for modo in ("prompt", "ferramenta", "parar"):
            res = self.rodar_guarda(modo, entrada="nao e json")
            self.assertEqual(res.returncode, 0, f"Falhou no modo {modo} com returncode {res.returncode}")

    def test_16_relatorio_sem_dados(self):
        res = self.rodar_guarda("relatorio")
        self.assertEqual(res.returncode, 0)
        self.assertIn("sem dados", res.stdout)


class TestInstalar(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.env = dict(os.environ)
        self.env["HOME"] = self.temp_dir
        self.env["USERPROFILE"] = self.temp_dir
        for k in ("MAESTRO_EU", "MAESTRO_GUARDA", "MAESTRO_LIMITE_AVISO", "MAESTRO_LIMITE_PARADA"):
            self.env.pop(k, None)

        self.patcher = unittest.mock.patch.dict(os.environ, {
            "HOME": self.temp_dir,
            "USERPROFILE": self.temp_dir,
        })
        self.patcher.start()
        for k in ("MAESTRO_EU", "MAESTRO_PROFUNDIDADE", "MAESTRO_GUARDA", "MAESTRO_LIMITE_AVISO", "MAESTRO_LIMITE_PARADA"):
            if k in os.environ:
                del os.environ[k]

        self.claude_dir = os.path.join(self.temp_dir, ".claude")
        os.makedirs(os.path.join(self.claude_dir, "skills"), exist_ok=True)
        self.settings_path = os.path.join(self.claude_dir, "settings.json")
        self.initial_settings = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}
                ]
            },
            "enabledPlugins": {"ai-memory@ai-memory": True}
        }
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(self.initial_settings, f)

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def rodar_instalar(self, *args):
        return subprocess.run(
            [sys.executable, CAMINHO_INSTALAR] + list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self.env,
        )

    def test_17_instala_hooks(self):
        res = self.rodar_instalar()
        self.assertEqual(res.returncode, 0)
        with open(self.settings_path, encoding="utf-8") as f:
            cfg = json.load(f)

        esperado = {
            "UserPromptSubmit": "prompt",
            "PostToolUse": "ferramenta",
            "Stop": "parar",
        }
        hooks = cfg.get("hooks", {})
        for evento, modo in esperado.items():
            grupos_guarda = [g for g in hooks.get(evento, [])
                             if any("guarda.py" in h.get("command", "") for h in g.get("hooks", []))]
            self.assertEqual(len(grupos_guarda), 1, f"Esperado exatamente 1 grupo em {evento}")
            cmd = grupos_guarda[0]["hooks"][0]["command"]
            self.assertTrue(cmd.rstrip().endswith(" " + modo), f"Comando de {evento} nao termina com o modo {modo}: {cmd}")

        self.assertEqual(hooks.get("PreToolUse"), self.initial_settings["hooks"]["PreToolUse"])
        self.assertEqual(cfg.get("enabledPlugins"), self.initial_settings["enabledPlugins"])

        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, ".maestro", "guarda.py")))
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, ".maestro", "maestro.py")))
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, ".claude", "skills", "maestro", "SKILL.md")))

    def test_18_idempotente(self):
        self.rodar_instalar()
        self.rodar_instalar()
        with open(self.settings_path, encoding="utf-8") as f:
            cfg = json.load(f)
        hooks = cfg.get("hooks", {})
        for evento in ("UserPromptSubmit", "PostToolUse", "Stop"):
            grupos_guarda = [g for g in hooks.get(evento, [])
                             if any("guarda.py" in h.get("command", "") for h in g.get("hooks", []))]
            self.assertEqual(len(grupos_guarda), 1, f"Esperado exatamente 1 grupo em {evento} apos 2 instalacoes")

    def test_19_sem_guarda_remove(self):
        self.rodar_instalar()
        res = self.rodar_instalar("--sem-guarda")
        self.assertEqual(res.returncode, 0)
        with open(self.settings_path, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg_str = json.dumps(cfg)
        self.assertNotIn("guarda.py", cfg_str)
        self.assertEqual(cfg.get("hooks", {}).get("PreToolUse"), self.initial_settings["hooks"]["PreToolUse"])

    def test_20_comando_instalado_funciona(self):
        self.rodar_instalar()
        with open(self.settings_path, encoding="utf-8") as f:
            cfg = json.load(f)
        cmd = cfg["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        prompt_longo = json.dumps({"session_id": "s_cmd", "cwd": "C:/x", "prompt": "este prompt tem mais de quarenta caracteres para testar"})
        res = subprocess.run(
            cmd,
            shell=True,
            input=prompt_longo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self.env,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("additionalContext", res.stdout)

    def test_52_skills_validar_instaladas(self):
        self.rodar_instalar()
        for mod, nome_esperado in (("opus", "claudevalidateopus"), ("fable", "claudevalidatefable")):
            caminho_skill = os.path.join(self.claude_dir, "skills", nome_esperado, "SKILL.md")
            self.assertTrue(os.path.exists(caminho_skill), f"Skill {nome_esperado} nao existe em {caminho_skill}")
            with open(caminho_skill, encoding="utf-8") as f:
                conteudo = f.read()
            self.assertIn(f"name: {nome_esperado}", conteudo)
            self.assertNotIn("{modelo}", conteudo)


class TestExecutorSemMemoria(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.patcher = unittest.mock.patch.dict(os.environ, {
            "HOME": self.temp_dir,
            "USERPROFILE": self.temp_dir,
        })
        self.patcher.start()
        for k in ("MAESTRO_EU", "MAESTRO_PROFUNDIDADE", "MAESTRO_GUARDA", "MAESTRO_LIMITE_AVISO", "MAESTRO_LIMITE_PARADA"):
            if k in os.environ:
                del os.environ[k]

        os.makedirs(os.path.join(self.temp_dir, ".maestro"), exist_ok=True)
        self.claude_dir = os.path.join(self.temp_dir, ".claude")
        os.makedirs(self.claude_dir, exist_ok=True)
        self.settings_path = os.path.join(self.claude_dir, "settings.json")
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump({"enabledPlugins": {"ai-memory@ai-memory": True, "outro@x": True}}, f)

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def importar_maestro(self):
        spec = importlib.util.spec_from_file_location("maestro", CAMINHO_MAESTRO)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        return modulo

    def test_21_config_executor_plugins_desligados(self):
        modulo = self.importar_maestro()
        caminho = modulo.config_executor()
        self.assertTrue(os.path.exists(caminho))
        with open(caminho, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertIn("enabledPlugins", cfg)
        self.assertFalse(cfg["enabledPlugins"].get("ai-memory@ai-memory"))
        self.assertFalse(cfg["enabledPlugins"].get("outro@x"))

    def test_22_config_executor_sem_settings_claude(self):
        if os.path.exists(self.settings_path):
            os.remove(self.settings_path)
        exec_settings = os.path.join(self.temp_dir, ".maestro", "executor-settings.json")
        if os.path.exists(exec_settings):
            os.remove(exec_settings)
        modulo = self.importar_maestro()
        caminho = modulo.config_executor()
        self.assertTrue(os.path.exists(caminho))
        with open(caminho, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertIn("enabledPlugins", cfg)
        self.assertFalse(cfg["enabledPlugins"].get("ai-memory@ai-memory"))

    def test_23_montar_claude(self):
        modulo = self.importar_maestro()
        with unittest.mock.patch.object(modulo.shutil, "which", return_value="claude"):
            cmd, env = modulo.montar({"cli": "claude", "modelo": "haiku"}, "p", None, 60)
        self.assertIn("--strict-mcp-config", cmd)
        self.assertIn("--settings", cmd)
        idx = cmd.index("--settings")
        caminho_cfg = modulo.config_executor()
        self.assertEqual(os.path.normpath(cmd[idx + 1]), os.path.normpath(caminho_cfg))

    def test_24_montar_ollama(self):
        modulo = self.importar_maestro()
        with unittest.mock.patch.object(modulo.shutil, "which", return_value="claude"):
            cmd, env = modulo.montar({"cli": "ollama", "modelo": "x"}, "p", None, 60)
        self.assertIn("--bare", cmd)
        self.assertIn("ANTHROPIC_BASE_URL", env)

    def test_33_montar_agy_leitura_mantem_plan_mode(self):
        # agy 1.5.12 ignora --mode plan quando ha --disable-slash-commands: a leitura poderia escrever no projeto
        modulo = self.importar_maestro()
        with unittest.mock.patch.object(modulo.shutil, "which", return_value="agy"):
            ler, _ = modulo.montar({"cli": "agy", "modelo": "m"}, "p", None, 60, leitura=True)
            exe, _ = modulo.montar({"cli": "agy", "modelo": "m"}, "p", None, 60)
        self.assertIn("plan", ler)
        self.assertNotIn("--disable-slash-commands", ler)
        self.assertIn("--disable-slash-commands", exe)
        self.assertNotIn("--mode", exe)

    def test_25_regras_proibem_delegar(self):
        modulo = self.importar_maestro()
        self.assertIn("nao use a skill maestro", modulo.REGRAS)


class TestCorrecoesExecutor(unittest.TestCase):
    """Regressoes achadas no uso real: verificacao vazia aprovada e modelo local em loop prendendo o job."""
    setUp = TestExecutorSemMemoria.setUp
    tearDown = TestExecutorSemMemoria.tearDown
    importar_maestro = TestExecutorSemMemoria.importar_maestro

    def test_26_verificacao_sem_teste_nao_e_ok(self):
        modulo = self.importar_maestro()
        for saida in ("\n----------------------------------------------------------------------\nRan 0 tests in 0.000s\n\nOK\n",
                      "NO TESTS RAN", "collected 0 items", "No tests found, exiting with code 1"):
            self.assertTrue(modulo.VAZIA_RE.search(saida), saida)
        for saida in ("Ran 1 test in 0.1s\n\nOK", "Ran 25 tests in 4.5s\n\nOK", "10 passed", "Todos os testes passaram."):
            self.assertFalse(modulo.VAZIA_RE.search(saida), saida)

    def test_27_ollama_tem_teto_de_saida(self):
        modulo = self.importar_maestro()
        with unittest.mock.patch.object(modulo.shutil, "which", return_value="claude"):
            _, env = modulo.montar({"cli": "ollama", "modelo": "x"}, "p", None, 60)
            self.assertEqual(env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "16000")
            _, env = modulo.montar({"cli": "ollama", "modelo": "x", "max_saida": 8000}, "p", None, 60)
            self.assertEqual(env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "8000")

    def test_28_ollama_travado_cede_a_vez(self):
        import types
        modulo = self.importar_maestro()
        wt = os.path.join(self.temp_dir, "wt")
        os.makedirs(wt)
        os.makedirs(modulo.job_dir("t1"), exist_ok=True)
        cat = {"ollama-x": {"cli": "ollama", "modelo": "m", "timeout": 900},
               "cc-x": {"cli": "claude", "modelo": "haiku"}}
        ok_json = json.dumps({"session_id": "s", "result": "RESULTADO: feito", "usage": {}})
        chamadas = []

        def falso_run(cmd, **kw):
            chamadas.append(kw.get("timeout"))
            if len(chamadas) == 1:
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
            return subprocess.CompletedProcess(cmd, 0, ok_json, "")

        with unittest.mock.patch.object(modulo.shutil, "which", return_value="claude"), \
                unittest.mock.patch.object(modulo, "garantir_ollama", return_value=True), \
                unittest.mock.patch.object(modulo, "esgotados", return_value={}), \
                unittest.mock.patch.object(modulo.subprocess, "run", side_effect=falso_run):
            r = modulo.executar({"job": "t1", "rodada": 1, "wt": wt, "sessao": None}, ["ollama-x", "cc-x"], cat,
                                types.SimpleNamespace(timeout=1800, horas_cota=5))
        self.assertEqual(r["exec"], "cc-x")
        self.assertEqual(chamadas, [960, 1860])  # o local usa o timeout proprio; o proximo, o do run
        with open(os.path.join(self.temp_dir, ".maestro", "historico.jsonl"), encoding="utf-8") as f:
            self.assertIn('"falha_local"', f.read())


class TestConvivenciaAiMemory(unittest.TestCase):
    """As regras do executor viraram habito do perfil do usuario no ai-memory ('nao use o maestro')."""
    setUp = TestExecutorSemMemoria.setUp
    tearDown = TestExecutorSemMemoria.tearDown
    importar_maestro = TestExecutorSemMemoria.importar_maestro

    def test_32_marcador_isola_executor_do_perfil(self):
        import tomllib
        modulo = self.importar_maestro()
        pasta = os.path.join(self.temp_dir, ".maestro", "wt")
        os.makedirs(pasta)
        modulo.garantir_marcador(pasta)
        with open(os.path.join(pasta, ".ai-memory.toml"), "rb") as f:
            cfg = tomllib.load(f)
        self.assertEqual(cfg["profile"], {"contribute": False, "consume": False})
        self.assertFalse(cfg["handoff"]["deliver_on_session_start"])
        self.assertNotIn("project", cfg)  # nao renomeia o projeto da sessao
        # nao sobrescreve um marcador que o usuario tenha ajustado
        with open(os.path.join(pasta, ".ai-memory.toml"), "w", encoding="utf-8") as f:
            f.write("[profile]\ncontribute = false\n")
        modulo.garantir_marcador(pasta)
        with open(os.path.join(pasta, ".ai-memory.toml"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "[profile]\ncontribute = false\n")


class TestRotaPorAcerto(unittest.TestCase):
    """Executor gratis que descarta muito custa token caro de revisao: desce na rota e cede a correcao."""
    setUp = TestExecutorSemMemoria.setUp
    tearDown = TestExecutorSemMemoria.tearDown
    importar_maestro = TestExecutorSemMemoria.importar_maestro

    def historico(self, linhas):
        with open(os.path.join(self.temp_dir, ".maestro", "historico.jsonl"), "a", encoding="utf-8") as f:
            for evento, eid, tipo, data in linhas:
                f.write(json.dumps({"evento": evento, "job": "j", "exec": eid, "tipo": tipo, "data": data}) + "\n")

    def test_29_fraco_vai_para_o_fim(self):
        import time
        hoje = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.historico([("descartar", "gratis", None, hoje)] * 4 + [("juntar", "gratis", None, hoje)]
                       + [("juntar", "flash", "implementar", hoje)] * 5)
        modulo = self.importar_maestro()
        self.assertEqual(modulo.ordenar_por_acerto(["gratis", "outro", "flash"], None), ["outro", "flash", "gratis"])
        # outro tipo nao herda o placar; todos fracos = nada muda
        self.assertEqual(modulo.ordenar_por_acerto(["gratis", "flash"], "docs"), ["gratis", "flash"])
        self.assertEqual(modulo.ordenar_por_acerto(["gratis"], None), ["gratis"])

    def test_30_amostra_pequena_e_historico_velho_nao_rebaixam(self):
        import time
        hoje = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.historico([("descartar", "novo", None, hoje)] * 4 + [("descartar", "velho", None, "2000-01-01T00:00:00")] * 9)
        modulo = self.importar_maestro()
        self.assertEqual(modulo.ordenar_por_acerto(["novo", "velho", "x"], None), ["novo", "velho", "x"])

    def test_31_correcao_troca_gratis_que_falhou(self):
        modulo = self.importar_maestro()
        cat = {"gratis": {"cota": "local"}, "flash": {"cota": "google"}}
        os.makedirs(modulo.job_dir("t2"), exist_ok=True)
        with open(os.path.join(modulo.job_dir("t2"), "tarefa-r1.md"), "w", encoding="utf-8") as f:
            f.write("TAREFA ORIGINAL")
        j = {"job": "t2", "rodada": 2, "exec_usado": "gratis", "falhou": "verificacao"}
        execs, texto = modulo.correcao(j, ["gratis", "flash"], cat, "conserte X", True)
        self.assertEqual(execs, ["flash", "gratis"])
        self.assertIn("TAREFA ORIGINAL", texto)
        self.assertIn("conserte X", texto)
        # pago que falhou, rodada que passou ou lista explicita: mesmo executor (ele vai primeiro), so a correcao
        for jj, cc, auto, esperado in ((dict(j, exec_usado="flash"), cat, True, ["flash", "gratis"]),
                                       (dict(j, falhou=None), cat, True, ["gratis", "flash"]),
                                       (j, cat, False, ["gratis", "flash"])):
            execs, texto = modulo.correcao(jj, ["gratis", "flash"], cc, "conserte X", auto)
            self.assertEqual(execs, esperado)
            self.assertNotIn("TAREFA ORIGINAL", texto)
        # --exec com outro executor, sem a sessao de quem comecou: recebe a tarefa original
        execs, texto = modulo.correcao(dict(j, falhou=None), ["flash"], cat, "conserte X", False)
        self.assertEqual(execs, ["flash"])
        self.assertIn("TAREFA ORIGINAL", texto)


class TestGanhoComparar(unittest.TestCase):
    setUp = TestExecutorSemMemoria.setUp
    tearDown = TestExecutorSemMemoria.tearDown
    importar_maestro = TestExecutorSemMemoria.importar_maestro

    def historico(self, eventos):
        with open(os.path.join(self.temp_dir, ".maestro", "historico.jsonl"), "a", encoding="utf-8") as f:
            for e in eventos:
                f.write(json.dumps(e) + "\n")

    def test_34_indicadores_exemplo(self):
        modulo = self.importar_maestro()
        ev = [
            {"evento": "run", "job": "A", "rodada": 1, "verificacao": False, "tarefa_c": 400, "devolvido_c": 400},
            {"evento": "run", "job": "A", "rodada": 2, "verificacao": True, "tarefa_c": 400, "devolvido_c": 400},
            {"evento": "run", "job": "B", "rodada": 1, "verificacao": True, "tarefa_c": 400, "devolvido_c": 400},
            {"evento": "juntar", "job": "A"},
            {"evento": "descartar", "job": "B"},
        ]
        res = modulo.indicadores(ev)
        self.assertEqual(res, {
            "jobs": 2,
            "primeira_ok": 50,
            "rodadas_media": 1.5,
            "juntado": 50,
            "custo_por_juntado": 600,
        })

    def test_35_indicadores_vazio(self):
        modulo = self.importar_maestro()
        res = modulo.indicadores([])
        self.assertEqual(res, {
            "jobs": 0,
            "primeira_ok": None,
            "rodadas_media": None,
            "juntado": None,
            "custo_por_juntado": None,
        })

    def test_36_ganho_comparar(self):
        import argparse
        import contextlib
        import io
        import time

        modulo = self.importar_maestro()
        agora = time.time()
        d_ant = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(agora - 10 * 86400))
        d_atu = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(agora - 2 * 86400))

        ev_ant = [
            {"evento": "run", "job": "pA", "exec": "agy-flash-high", "rodada": 1, "verificacao": False, "tarefa_c": 1000, "devolvido_c": 1000, "tokens_in": 100, "tokens_out": 100, "data": d_ant},
            {"evento": "run", "job": "pA", "exec": "agy-flash-high", "rodada": 2, "verificacao": True, "tarefa_c": 1000, "devolvido_c": 1000, "tokens_in": 100, "tokens_out": 100, "data": d_ant},
            {"evento": "juntar", "job": "pA", "data": d_ant},
            {"evento": "run", "job": "pB", "exec": "agy-flash-high", "rodada": 1, "verificacao": False, "tarefa_c": 1000, "devolvido_c": 1000, "tokens_in": 100, "tokens_out": 100, "data": d_ant},
            {"evento": "descartar", "job": "pB", "data": d_ant},
        ]
        ev_atu = [
            {"evento": "run", "job": "cA", "exec": "agy-flash-high", "rodada": 1, "verificacao": True, "tarefa_c": 400, "devolvido_c": 400, "tokens_in": 100, "tokens_out": 100, "data": d_atu},
            {"evento": "juntar", "job": "cA", "data": d_atu},
            {"evento": "run", "job": "cB", "exec": "agy-flash-high", "rodada": 1, "verificacao": True, "tarefa_c": 400, "devolvido_c": 400, "tokens_in": 100, "tokens_out": 100, "data": d_atu},
            {"evento": "juntar", "job": "cB", "data": d_atu},
        ]
        self.historico(ev_ant + ev_atu)

        a = argparse.Namespace(cmd="ganho", dias=7.0, comparar=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            modulo.cmd_ganho(a)
        out = buf.getvalue()

        self.assertIn("COMPARACAO (ultimos 7 dias x 7 dias anteriores)", out)
        self.assertIn("melhor", out)

    def test_37_ganho_comparar_dias_zero(self):
        import argparse
        import contextlib
        import io

        modulo = self.importar_maestro()
        a = argparse.Namespace(cmd="ganho", dias=0.0, comparar=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            modulo.cmd_ganho(a)
        out = buf.getvalue()
        self.assertIn("--comparar precisa de --dias maior que 0", out)
        self.assertNotIn("COMPARACAO", out)


class TestCorrecaoAutomatica(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.patcher = unittest.mock.patch.dict(os.environ, {
            "HOME": self.temp_dir,
            "USERPROFILE": self.temp_dir,
        })
        self.patcher.start()
        for k in ("MAESTRO_EU", "MAESTRO_PROFUNDIDADE", "MAESTRO_GUARDA", "MAESTRO_LIMITE_AVISO", "MAESTRO_LIMITE_PARADA"):
            if k in os.environ:
                del os.environ[k]
        os.makedirs(os.path.join(self.temp_dir, ".maestro"), exist_ok=True)
        self.repo = os.path.join(self.temp_dir, "repo")
        os.makedirs(self.repo, exist_ok=True)
        subprocess.run(["git", "init"], cwd=self.repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=self.repo, capture_output=True, check=True)
        with open(os.path.join(self.repo, "valor.txt"), "w", encoding="utf-8") as f:
            f.write("inicial")
        subprocess.run(["git", "add", "."], cwd=self.repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, capture_output=True, check=True)

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def importar_maestro(self):
        spec = importlib.util.spec_from_file_location("maestro", CAMINHO_MAESTRO)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        return modulo

    def rodar_run(self, valores, extras=None, verif=None):
        modulo = self.importar_maestro()
        cat = {"fake": {"cli": "claude", "nivel": 2, "cota": "anthropic", "custo": "baixo"}}
        chamadas = [0]
        vals = list(valores)

        def falso_executar(j, execs, cat_arg, a_arg, passo=0):
            chamadas[0] += 1
            if vals:
                v = vals.pop(0)
                with open(os.path.join(j["wt"], "valor.txt"), "w", encoding="utf-8") as f:
                    f.write(v)
            return {
                "exec": "fake",
                "sessao": None,
                "texto": "RESULTADO: ok",
                "tin": 10,
                "tout": 5,
                "erro": None,
                "rc": 0,
                "estourou": False,
            }

        if verif is None:
            cmd_verif = (
                f'"{sys.executable}" -c "import sys; '
                "v=open('valor.txt').read().strip(); "
                "print('erro: valor', v); "
                "sys.exit(0 if v=='certo' else 1)\""
            )
        else:
            cmd_verif = verif

        argv = [
            "maestro.py",
            "run",
            "--exec",
            "fake",
            "--dir",
            self.repo,
            "--tipo",
            "implementar",
            "--verificar",
            cmd_verif,
        ]
        if extras:
            argv.extend(extras)
        argv.append("tarefa de teste com texto suficiente para passar na validacao de tamanho")

        buf = io.StringIO()
        codigo = None
        with unittest.mock.patch.object(modulo, "catalogo", return_value=cat), \
             unittest.mock.patch.object(modulo, "esgotados", return_value={}), \
             unittest.mock.patch.object(modulo, "executar", side_effect=falso_executar), \
             unittest.mock.patch.object(sys, "argv", argv), \
             contextlib.redirect_stdout(buf):
            try:
                modulo.main()
            except SystemExit as e:
                codigo = e.code

        return codigo, chamadas[0], buf.getvalue(), modulo

    def test_38_correcao_automatica_passa_segunda_rodada(self):
        codigo, chamadas, out, modulo = self.rodar_run(["errado", "certo"])
        self.assertEqual(codigo, 0)
        self.assertEqual(chamadas, 2)
        self.assertIn("correcao automatica:", out)
        self.assertIn("OK", out)

        with open(modulo.HISTORICO, encoding="utf-8") as f:
            runs = [json.loads(line) for line in f if '"evento": "run"' in line]
        self.assertEqual(len(runs), 2)
        r1, r2 = runs[0], runs[1]
        self.assertEqual(r1["rodada"], 1)
        self.assertFalse(r1["verificacao"])
        self.assertFalse(r1["auto"])
        self.assertGreater(r1["tarefa_c"], 0)

        self.assertEqual(r2["rodada"], 2)
        self.assertTrue(r2["verificacao"])
        self.assertTrue(r2["auto"])
        self.assertEqual(r2["tarefa_c"], 0)

    def test_39_correcao_automatica_para_sem_progresso(self):
        codigo, chamadas, out, _ = self.rodar_run(["errado", "errado", "errado"])
        self.assertEqual(codigo, 3)
        self.assertEqual(chamadas, 2)
        self.assertIn("sem progresso", out)

    def test_40_correcao_automatica_tentativas_zero(self):
        codigo, chamadas, out, _ = self.rodar_run(["errado"], extras=["--tentativas", "0"])
        self.assertEqual(codigo, 3)
        self.assertEqual(chamadas, 1)
        self.assertNotIn("correcao automatica", out)

    def test_41_correcao_automatica_orcamento_atingido(self):
        codigo, chamadas, out, _ = self.rodar_run(["errado", "certo"], extras=["--orcamento", "10"])
        self.assertEqual(codigo, 3)
        self.assertEqual(chamadas, 1)
        self.assertIn("orcamento", out)

    def test_42_correcao_automatica_limite_tres_rodadas(self):
        codigo, chamadas, out, _ = self.rodar_run(["e1", "e2", "e3", "e4"], extras=["--tentativas", "5"])
        self.assertEqual(codigo, 3)
        self.assertEqual(chamadas, 3)
        self.assertIn("3 rodadas", out)

    def test_43_correcao_automatica_sem_mudanca_no_worktree(self):
        cmd_verif = f'"{sys.executable}" -c "pass"'
        codigo, chamadas, out, _ = self.rodar_run([], verif=cmd_verif)
        self.assertEqual(codigo, 3)
        self.assertEqual(chamadas, 2)
        self.assertIn("nao alterou nenhum arquivo", out)

    def test_44_assinatura_ignora_tempos_e_distingue_erros(self):
        modulo = self.importar_maestro()
        s1 = modulo.assinatura("FAILED test_x in 0.12s")
        s2 = modulo.assinatura("FAILED test_x in 0.40s")
        self.assertEqual(s1, s2)
        e1 = modulo.assinatura("AssertionError: 1 != 2")
        e2 = modulo.assinatura("AssertionError: 1 != 3")
        self.assertNotEqual(e1, e2)


class TestValidar(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.patcher = unittest.mock.patch.dict(os.environ, {
            "HOME": self.temp_dir,
            "USERPROFILE": self.temp_dir,
        })
        self.patcher.start()
        for k in ("MAESTRO_EU", "MAESTRO_PROFUNDIDADE", "MAESTRO_GUARDA", "MAESTRO_LIMITE_AVISO", "MAESTRO_LIMITE_PARADA"):
            if k in os.environ:
                del os.environ[k]
        os.makedirs(os.path.join(self.temp_dir, ".maestro"), exist_ok=True)
        self.repo = os.path.join(self.temp_dir, "repo")
        os.makedirs(self.repo, exist_ok=True)
        subprocess.run(["git", "init"], cwd=self.repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=self.repo, capture_output=True, check=True)
        self.arq = os.path.join(self.repo, "a.py")
        with open(self.arq, "w", encoding="utf-8") as f:
            f.write("def foo():\n    return 1\n")
        subprocess.run(["git", "add", "."], cwd=self.repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, capture_output=True, check=True)

        with open(self.arq, "w", encoding="utf-8") as f:
            f.write("def foo():\n    return 2\n")

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def importar_maestro(self):
        spec = importlib.util.spec_from_file_location("maestro", CAMINHO_MAESTRO)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        return modulo

    def rodar_validar(self, pedido, resposta_texto="", extras=None, erro=None):
        modulo = self.importar_maestro()
        chamadas = []

        def falso_perguntar(execs, cat, template, texto, pasta, a):
            chamadas.append((execs, cat, template, texto, pasta, a))
            eid = execs[0] if execs else "validar-opus"
            return (eid, resposta_texto, 100, 50, erro, 2.0)

        argv = ["maestro.py", "validar", "--dir", self.repo]
        if extras:
            argv.extend(extras)
        if pedido is not None:
            argv.append(pedido)

        buf = io.StringIO()
        codigo = None
        with unittest.mock.patch.object(modulo, "perguntar", side_effect=falso_perguntar), \
             unittest.mock.patch.object(sys, "argv", argv), \
             contextlib.redirect_stdout(buf):
            try:
                modulo.main()
            except SystemExit as e:
                codigo = e.code

        return codigo, chamadas, buf.getvalue(), modulo

    def test_45_validar_reprovado(self):
        pedido = "pedido do usuario com mais de vinte caracteres para validacao"
        resp = "VEREDITO: REPROVADO\nPROBLEMAS: 1. [bloqueia] a.py:1 x"
        codigo, chamadas, out, modulo = self.rodar_validar(pedido, resposta_texto=resp)
        self.assertEqual(codigo, 3)
        self.assertEqual(len(chamadas), 1)

        texto_passado = chamadas[0][3]
        self.assertIn(pedido, texto_passado)
        m = re.search(r"Diff completo \([^)]+\): (.*?diff\.patch)", texto_passado)
        self.assertIsNotNone(m, f"caminho de diff.patch nao encontrado no texto: {texto_passado}")
        caminho_diff = m.group(1).strip()
        self.assertTrue(os.path.isfile(caminho_diff), f"diff.patch nao existe em {caminho_diff}")
        with open(caminho_diff, encoding="utf-8") as f:
            conteudo_diff = f.read()
        self.assertIn("return 2", conteudo_diff)

        with open(modulo.HISTORICO, encoding="utf-8") as f:
            vals = [json.loads(line) for line in f if '"evento": "validacao"' in line]
        self.assertEqual(len(vals), 1)
        self.assertEqual(vals[0]["veredito"], "REPROVADO")
        self.assertEqual(vals[0]["exec"], "validar-opus")

    def test_46_validar_aprovado_e_ressalvas(self):
        pedido = "pedido do usuario com mais de vinte caracteres para validacao"
        c1, _, _, _ = self.rodar_validar(pedido, resposta_texto="VEREDITO: **APROVADO COM RESSALVAS**\nRESUMO: ok")
        self.assertEqual(c1, 0)
        c2, _, _, _ = self.rodar_validar(pedido, resposta_texto="VEREDITO: APROVADO\nRESUMO: tudo certo")
        self.assertEqual(c2, 0)

    def test_47_validar_sem_veredito(self):
        pedido = "pedido do usuario com mais de vinte caracteres para validacao"
        codigo, _, out, _ = self.rodar_validar(pedido, resposta_texto="RESUMO: apenas resumo sem veredito")
        self.assertEqual(codigo, 1)
        self.assertIn("trate como nao validado", out)

    def test_48_validar_repo_sem_mudanca(self):
        with open(self.arq, "w", encoding="utf-8") as f:
            f.write("def foo():\n    return 1\n")
        pedido = "pedido do usuario com mais de vinte caracteres para validacao"
        codigo, chamadas, out, _ = self.rodar_validar(pedido, resposta_texto="VEREDITO: APROVADO")
        self.assertEqual(len(chamadas), 0)
        self.assertIn("nada mudou", str(codigo) + out)

    def test_49_validar_pedido_curto(self):
        codigo, chamadas, _, _ = self.rodar_validar("pedido curto")
        self.assertEqual(len(chamadas), 0)

    def test_50_validar_modelo_fable(self):
        pedido = "pedido do usuario com mais de vinte caracteres para validacao"
        codigo, chamadas, _, _ = self.rodar_validar(pedido, resposta_texto="VEREDITO: APROVADO", extras=["--modelo", "fable"])
        self.assertEqual(len(chamadas), 1)
        execs, cat, _, _, _, _ = chamadas[0]
        self.assertEqual(execs, ["validar-fable"])
        self.assertEqual(cat["validar-fable"]["modelo"], "claude-fable-5-2")
        self.assertEqual(cat["validar-fable"]["esforco"], "medium")

    def test_51_montar_validador_opus(self):
        modulo = self.importar_maestro()
        with unittest.mock.patch.object(modulo.shutil, "which", return_value="claude"):
            cmd, env = modulo.montar(modulo.VALIDADORES["opus"], "prompt teste", None, 60, leitura=True)
        self.assertIn("--effort", cmd)
        self.assertIn("medium", cmd)
        self.assertIn("--model", cmd)
        self.assertIn("claude-opus-5-5", cmd)
        self.assertIn("--disallowedTools", cmd)


class TestJuntar(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.patcher = unittest.mock.patch.dict(os.environ, {
            "HOME": self.temp_dir,
            "USERPROFILE": self.temp_dir,
        })
        self.patcher.start()
        for k in ("MAESTRO_EU", "MAESTRO_PROFUNDIDADE", "MAESTRO_GUARDA", "MAESTRO_LIMITE_AVISO", "MAESTRO_LIMITE_PARADA"):
            if k in os.environ:
                del os.environ[k]
        os.makedirs(os.path.join(self.temp_dir, ".maestro"), exist_ok=True)
        self.repo = os.path.join(self.temp_dir, "repo")
        os.makedirs(self.repo, exist_ok=True)
        subprocess.run(["git", "init"], cwd=self.repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=self.repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "diff.context", "35"], cwd=self.repo, capture_output=True, check=True)

        self.linhas = [f"linha {i}\n" for i in range(1, 31)]
        self.arq = os.path.join(self.repo, "arquivo.txt")
        with open(self.arq, "w", encoding="utf-8") as f:
            f.writelines(self.linhas)
        subprocess.run(["git", "add", "."], cwd=self.repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, capture_output=True, check=True)

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def importar_maestro(self):
        spec = importlib.util.spec_from_file_location("maestro", CAMINHO_MAESTRO)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        return modulo

    def test_53_juntar_projeto_alterado(self):
        modulo = self.importar_maestro()
        job_id = "job_juntar_teste"
        wt, base, aviso = modulo.criar_worktree(self.repo, job_id)
        j = {
            "job": job_id,
            "repo": self.repo,
            "wt": wt,
            "base": base,
            "rodada": 1,
            "exec_usado": "fake",
            "tipo": "implementar",
        }
        modulo.salvar_job(j)

        # executor altera a linha 2 do arquivo no worktree
        with open(os.path.join(wt, "arquivo.txt"), "r", encoding="utf-8") as f:
            wt_linhas = f.readlines()
        wt_linhas[1] = "linha 2 alterada\n"
        with open(os.path.join(wt, "arquivo.txt"), "w", encoding="utf-8") as f:
            f.writelines(wt_linhas)

        # antes de juntar, altera sem commitar a linha 29 do mesmo arquivo no repo real
        with open(self.arq, "r", encoding="utf-8") as f:
            repo_linhas = f.readlines()
        repo_linhas[28] = "linha 29 alterada\n"
        with open(self.arq, "w", encoding="utf-8") as f:
            f.writelines(repo_linhas)

        buf = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["maestro.py", "juntar", job_id]), \
             contextlib.redirect_stdout(buf):
            modulo.main()

        out = buf.getvalue()
        self.assertIn("contexto reduzido", out)

        with open(self.arq, encoding="utf-8") as f:
            conteudo_final = f.read()
        self.assertIn("linha 2 alterada", conteudo_final)
        self.assertIn("linha 29 alterada", conteudo_final)


if __name__ == "__main__":
    unittest.main()


def _carregar_maestro():
    spec = importlib.util.spec_from_file_location("maestro_folga", CAMINHO_MAESTRO)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestFolgaLocal(unittest.TestCase):
    """Modelo local que nao cabe: tem prioridade. Janela 'fechar' ou sem resposta fecha, roda e reabre no fim;
    'nao' segue a rota sem o local; sem janela para com saida 5; --pular-local nem pergunta."""

    def setUp(self):
        self.m = _carregar_maestro()
        GB = self.m.GB
        self.cat = {"ollama-grande": {"cli": "ollama", "modelo": "grande:1"},
                    "ollama-pequeno": {"cli": "ollama", "modelo": "pequeno:1"},
                    "agy-x": {"cli": "agy", "modelo": "flash"}}
        tamanhos = {"grande:1": 20 * GB, "pequeno:1": 2 * GB}
        self.mem = (8 * GB, 32 * GB, 4 * GB, 16 * GB)  # RAM livre, total, VRAM livre, total
        self.apps = {"chrome": (30 * GB, [11, 12], [("C:/x/chrome.exe", '"C:/x/chrome.exe"')]),
                     "discord": (1 * GB, [13], [("C:/x/discord.exe", "")])}

        def api(url, rota):
            return [] if rota == "ps" else [{"name": n, "size": s} for n, s in tamanhos.items()]
        for alvo, valor in (("garantir_ollama", lambda url: True), ("_ollama_api", api),
                            ("memoria_livre", lambda: self.mem), ("apps_liberaveis", lambda: self.apps),
                            ("registrar", unittest.mock.MagicMock()), ("fechar_apps", unittest.mock.MagicMock()),
                            ("guardar_para_reabrir", unittest.mock.MagicMock()),
                            ("reabrir_apps", unittest.mock.MagicMock())):
            p = unittest.mock.patch.object(self.m, alvo, valor)
            p.start()
            self.addCleanup(p.stop)
        p = unittest.mock.patch("atexit.register")  # nunca reabrir apps de verdade no fim dos testes
        self.atexit = p.start()
        self.addCleanup(p.stop)

    def _janela(self, resposta):
        p = unittest.mock.patch.object(self.m, "janela_liberar", unittest.mock.MagicMock(return_value=resposta))
        p.start()
        self.addCleanup(p.stop)

    def _cabe_depois_de_fechar(self):
        GB = self.m.GB

        def fechou(*a, **k):
            self.mem = (30 * GB, 32 * GB, 4 * GB, 16 * GB)
            return [{"nome": "chrome", "alvos": []}]
        self.m.fechar_apps.side_effect = fechou

    def test_sem_janela_para_com_saida_5(self):
        with self.assertRaises(SystemExit) as c, unittest.mock.patch("builtins.print"):
            self.m.checar_locais(["ollama-grande", "agy-x"], self.cat, pular=False, janela=False)
        self.assertEqual(c.exception.code, 5)
        self.m.fechar_apps.assert_not_called()

    def test_janela_que_nao_abre_cai_na_saida_5(self):
        self._janela("sem_janela")
        with self.assertRaises(SystemExit) as c, unittest.mock.patch("builtins.print"):
            self.m.checar_locais(["ollama-grande", "agy-x"], self.cat, False)
        self.assertEqual(c.exception.code, 5)
        self.m.fechar_apps.assert_not_called()

    def test_pular_local_tira_so_quem_nao_cabe(self):
        with unittest.mock.patch("builtins.print"):
            fica = self.m.checar_locais(["ollama-grande", "ollama-pequeno", "agy-x"], self.cat, pular=True)
        self.assertEqual(fica, ["ollama-pequeno", "agy-x"])

    def test_cabe_ou_nao_local_segue_igual(self):
        self.assertEqual(self.m.checar_locais(["ollama-pequeno", "agy-x"], self.cat, False), ["ollama-pequeno", "agy-x"])
        self.assertEqual(self.m.checar_locais(["agy-x", "ollama-grande"], self.cat, False), ["agy-x", "ollama-grande"])

    def test_modelo_ja_carregado_cabe(self):
        with unittest.mock.patch.object(self.m, "_ollama_api", lambda url, rota: [{"name": "grande:1", "size": 1}]):
            self.assertTrue(self.m.folga_local(self.cat["ollama-grande"])["cabe"])

    def test_nao_fechar_segue_a_rota_sem_fechar(self):
        self._janela("nao")
        with unittest.mock.patch("builtins.print"):
            fica = self.m.checar_locais(["ollama-grande", "ollama-pequeno", "agy-x"], self.cat, False)
        self.assertEqual(fica, ["ollama-pequeno", "agy-x"])
        self.m.fechar_apps.assert_not_called()

    def test_fechar_ou_sem_resposta_fecha_o_necessario_roda_e_agenda_reabrir(self):
        for resp in ("fechar", "tempo"):
            self.m.fechar_apps.reset_mock()
            self.atexit.reset_mock()
            self.mem = (8 * self.m.GB, 32 * self.m.GB, 4 * self.m.GB, 16 * self.m.GB)
            self._janela(resp)
            self._cabe_depois_de_fechar()
            with unittest.mock.patch("builtins.print"):
                fica = self.m.checar_locais(["ollama-grande", "agy-x"], self.cat, False)
            self.assertEqual(fica, ["ollama-grande", "agy-x"], resp)
            self.m.fechar_apps.assert_called_once_with(["chrome"], forcar=True)  # so o necessario
            self.m.guardar_para_reabrir.assert_called()
            self.assertIs(self.atexit.call_args[0][0], self.m._ao_terminar)

    def test_nao_coube_nem_depois_de_fechar_reabre_na_hora(self):
        self._janela("tempo")
        self.m.fechar_apps.return_value = [{"nome": "chrome", "alvos": []}]
        with unittest.mock.patch("builtins.print"):
            fica = self.m.checar_locais(["ollama-grande", "agy-x"], self.cat, False)
        self.assertEqual(fica, ["agy-x"])
        self.m.reabrir_apps.assert_called_once()
        self.atexit.assert_not_called()

    def test_nem_fechando_tudo_cabe_nao_fecha_nem_pergunta(self):
        self.apps = {"discord": (1 * self.m.GB, [13], [])}
        self._janela("fechar")
        with unittest.mock.patch("builtins.print"):
            fica = self.m.checar_locais(["ollama-grande", "agy-x"], self.cat, False)
        self.assertEqual(fica, ["agy-x"])
        self.m.janela_liberar.assert_not_called()
        self.m.fechar_apps.assert_not_called()


class TestAppsLiberaveis(unittest.TestCase):
    def test_protege_sistema_terminal_e_a_propria_cadeia(self):
        m = _carregar_maestro()
        eu, pai = os.getpid(), 999001
        MB = 1024 ** 2
        procs = [(eu, pai, "python.exe", 50 * MB, 1), (pai, 4, "Code.exe", 900 * MB, 1),
                 (999002, pai, "Code.exe", 300 * MB, 1), (999003, 1, "chrome.exe", 800 * MB, 1),
                 (999004, 1, "chrome.exe", 400 * MB, 1), (999005, 1, "svchost.exe", 900 * MB, 1),
                 (999006, 1, "Servico.exe", 900 * MB, 0), (999007, 1, "claude.exe", 900 * MB, 1)]
        apps = m.apps_liberaveis(procs)
        self.assertEqual(list(apps), ["chrome"])  # Code e ancestral; svchost/claude protegidos; sessao 0 e servico
        self.assertEqual(sorted(apps["chrome"][1]), [999003, 999004])

    def test_agrupa_pelo_app_que_abriu_e_guarda_como_reabrir(self):
        m = _carregar_maestro()
        MB = 1024 ** 2
        procs = [(500, 4, "explorer.exe", 100 * MB, 1, "", ""),
                 (600, 500, "steam.exe", 100 * MB, 1, "C:/s/steam.exe", '"C:/s/steam.exe" -silent'),
                 (601, 600, "steamwebhelper.exe", 300 * MB, 1, "C:/s/swh.exe", "swh --type=renderer"),
                 (700, 500, "SearchHost.exe", 100 * MB, 1, "", ""),
                 (701, 700, "msedgewebview2.exe", 400 * MB, 1, "C:/e/wv.exe", "wv")]
        apps = m.apps_liberaveis(procs)
        self.assertEqual(list(apps), ["steam"])  # webview do menu Iniciar (protegido) fica de fora
        self.assertEqual(sorted(apps["steam"][1]), [600, 601])
        self.assertEqual(apps["steam"][2], [("C:/s/steam.exe", '"C:/s/steam.exe" -silent')])


class TestReabrir(unittest.TestCase):
    def test_reabre_so_os_do_dono_e_navegador_volta_com_as_abas(self):
        m = _carregar_maestro()
        pasta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, pasta, True)
        exe = os.path.join(pasta, "chrome.exe")
        open(exe, "w").close()
        m.REABRIR = os.path.join(pasta, "reabrir.json")
        m.guardar_para_reabrir([{"nome": "chrome", "alvos": [[exe, f'"{exe}"']]}], "eu")
        m.guardar_para_reabrir([{"nome": "discord", "alvos": [[exe, ""]]}], "outro")
        with unittest.mock.patch("subprocess.Popen") as popen, unittest.mock.patch.object(m, "registrar"), \
                unittest.mock.patch("builtins.print"):
            m.reabrir_apps("eu")
        self.assertEqual(popen.call_count, 1)
        self.assertIn("--restore-last-session", popen.call_args[0][0])
        self.assertEqual([i["nome"] for i in m.ler_json(m.REABRIR, [])], ["discord"])
