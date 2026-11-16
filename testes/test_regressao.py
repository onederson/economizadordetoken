import importlib.util
import json
import os
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
        for k in ("MAESTRO_EU", "MAESTRO_GUARDA", "MAESTRO_LIMITE_AVISO", "MAESTRO_LIMITE_PARADA"):
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
        for k in ("MAESTRO_EU", "MAESTRO_GUARDA", "MAESTRO_LIMITE_AVISO", "MAESTRO_LIMITE_PARADA"):
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


class TestExecutorSemMemoria(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.patcher = unittest.mock.patch.dict(os.environ, {
            "HOME": self.temp_dir,
            "USERPROFILE": self.temp_dir,
        })
        self.patcher.start()
        for k in ("MAESTRO_EU", "MAESTRO_GUARDA", "MAESTRO_LIMITE_AVISO", "MAESTRO_LIMITE_PARADA"):
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


if __name__ == "__main__":
    unittest.main()
