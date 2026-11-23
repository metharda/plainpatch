# Hero: preview a change, apply it, read the audit log, undo it, and see a vague request refused.
# Real time against a real model (qwen2.5:3b on an RTX 4070, Ollama). Nothing is cut or mocked.
Output docs/demo/hero.gif
Source docs/demo/_style.tape

Hide
Type "export PS1='demo$ ' CONFIGBOT_URL=http://127.0.0.1:7000; alias configbot='python3 cli/configbot.py'; clear"
Enter
Sleep 500ms
Show

Type 'configbot "lower the orders cpu limit to 80%"'
Sleep 500ms
Enter
Wait+Line@1m /demo\$\s*$/
Sleep 3s

Type 'configbot "lower the orders cpu limit to 80%" --apply --author arda'
Sleep 500ms
Enter
Wait+Line@1m /demo\$\s*$/
Sleep 3s

Type "configbot history orders"
Sleep 500ms
Enter
Wait+Line@30s /demo\$\s*$/
Sleep 2500ms

Type "configbot undo orders --author arda"
Sleep 500ms
Enter
Wait+Line@30s /demo\$\s*$/
Sleep 3s

Type 'configbot "make orders faster"'
Sleep 500ms
Enter
Wait+Line@1m /demo\$\s*$/
Sleep 6s
