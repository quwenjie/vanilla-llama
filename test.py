import sys
import time
import json
import torch
import torch.distributed as dist
import datetime
from accelerate import init_empty_weights, load_checkpoint_and_dispatch
import os
from llama import ModelArgs, Tokenizer, Transformer, LLaMA, is_valid_layer


class LLaMAInference:
    def __init__(self, llama_path, model, device_map="auto", **kwargs):

        state_dict = os.path.join(llama_path, model, "state_dict.pth")
        params_file = os.path.join(llama_path, model, "params.json")
        tokenizer_path = os.path.join(llama_path, "tokenizer.model")

        assert os.path.exists(os.path.join(llama_path, model)), f"Model {model} does not exist"
        assert os.path.exists(state_dict), f"Model {model} does not exist"
        assert os.path.exists(params_file), f"Model {model} does not exist"
        assert os.path.exists(tokenizer_path), f"Missing tokenizer in {llama_path}"

        with open(params_file, "r") as f:
            params = json.load(f)

        model_args = dict(
            max_seq_len=2048,
            max_batch_size=2,
            **params
        )
        model_args.update(kwargs)
        model_args = ModelArgs(**model_args)

        self.tokenizer = Tokenizer(model_path=tokenizer_path)
        model_args.vocab_size = self.tokenizer.n_words

        with init_empty_weights():
            torch.set_default_tensor_type(torch.HalfTensor)
            model = Transformer(model_args)

        torch.set_default_tensor_type(torch.FloatTensor)

        self.model = load_checkpoint_and_dispatch(
            model,
            state_dict,
            device_map=device_map,
            no_split_module_classes=["TransformerBlock"]
        )

        self.generator = LLaMA(self.model, self.tokenizer)

    def generate(self, texts, temperature=0.8, top_p=0.95, max_length=5, stop_ids=None, stop_words=None):
        start_time = time.time()
        results, stats = self.generator.generate(
            texts,
            max_gen_len=max_length,
            temperature=temperature,
            top_p=top_p,
            stop_ids=stop_ids,
            stop_words=stop_words
        )
        end_time = time.time()
        stats["total_seconds"] = end_time - start_time
        stats["toks"] =  max(stats["num_generated_tokens"])
        return results, stats

print(dist.is_nccl_available())
dist.init_process_group(backend="nccl", init_method="env://", timeout=datetime.timedelta(seconds=1800), world_size=int(os.environ.get("WORLD_SIZE")), rank=int(os.environ.get("WORLD_RANK")), store=None, group_name='', pg_options=None)
world_size=dist.get_world_size()
rank=dist.get_rank()
modelname=sys.argv[1]
maxlen=int(sys.argv[2])
path=f"/scratch/llama/models/{modelname}_vanilla"


with open(os.path.join(path, modelname, "params.json"), "r") as f:
    params = json.load(f)
    LAYERNUM = params['n_layers']

cpu=torch.device("cpu")
gpu=torch.device("cuda")
device_map={"tok_embeddings":gpu,"norm":gpu,"output":gpu}
for i in range(LAYERNUM):
    if is_valid_layer(i,rank,world_size,LAYERNUM):
        device_map[f"layers.{i}"]=gpu
    else:
        device_map[f"layers.{i}"]=cpu

llama = LLaMAInference(path, modelname, device_map=device_map)
print('finish load model!')

for i in range(1):
    gen, stats= llama.generate(["I believe the meaning of life is","The solution of one plus seventeen is"], temperature=0, max_length=maxlen)
    print(gen)