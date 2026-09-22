# Vocabulario de segmentación

`cl100k_base.tiktoken` proviene del [vocabulario oficial de OpenAI](https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken).
SHA-256: `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`.
Las reglas BPE corresponden a [tiktoken 0.12.0](https://github.com/openai/tiktoken/blob/0.12.0/tiktoken_ext/openai_public.py), con licencia MIT incluida.

Se incluye localmente para que desarrollo, CI y producción no descarguen modelos al ejecutar el chunker.
Esta medida de segmentación no equivale al contador de Gemini; el adaptador del proveedor debe comprobar sus propios límites antes de enviar contenido.
