# jtools

The umbrella command.

```console
$ jtools list
j-tools: semantic judgment for the shell

  jgrep   print lines that fit a description
  jsort   sort lines by how well they fit a description
  ...

$ jtools doctor
jev-tools 0.1.1  python 3.12.4
config dir: /home/me/.config/jev   cache dir: /home/me/.cache/jev
  typesafe    no key      TYPESAFE_API_KEY
  openrouter  no key      OPENROUTER_API_KEY
  vercel      key found   AI_GATEWAY_API_KEY
  gateway     no key      JEV_GATEWAY_API_KEY

using vercel at https://ai-gateway.vercel.sh/v4/ai/evaluation-model with key vck_…9f2a, model typesafe-ai/jev
ok: one call in 412 ms, 283 input tokens, $0.0000119, answered by typesafe-ai/jev
```

| command | does |
|---|---|
| `jtools list` | the ten tools and what each decides (also the default) |
| `jtools doctor [--api NAME] [--model ID]` | check which keys are present, warn about a key file anyone can read, make one real call, report latency, tokens and cost. Exit 0 ok, 3 no or bad key, 4 call failed |
| `jtools version` | print the version |
