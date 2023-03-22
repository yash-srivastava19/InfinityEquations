# First load the dataset
import torch
import torch.nn as nn
from torch.nn import functional as F

with open('math.txt', 'r', encoding='utf-8') as f:   ## add path to the dataset here.
  text = f.read()

chars = sorted(list(set(text)))  # possible elements from the dataset.
vocab_size = len(chars)

print(vocab_size)

# Tokenize the set(Character level tokenizer)
string_toInt = {ch: i for i, ch in enumerate(chars)}
int_toString = {i: ch for i, ch in enumerate(chars)}

# Forward and Reverse Mapping(String to Integer). There are different schemes, Google uses sentencepiece,
# OpenAI uses tiktoken
encode = lambda string: [string_toInt[c] for c in string]
decode = lambda lst: ''.join([int_toString[i] for i in lst])

data = torch.tensor(encode(text), dtype=torch.long)
N = int(len(data)*0.9)
trainData = data[N:]
valData = data[:N]


context_length = 10
batch_size = 4     # how many independent sequences will we process in parallel
eval_interval = 200
torch.manual_seed(69)
n_embd = 32
dropout = 0.18
n_layer = 3
n_head = 3
max_iters = 30
eval_iters = 50
learning_rate = 3e-4
device = 'cuda' if torch.cuda.is_available() else 'cpu'


@torch.no_grad()
def estimate_loss():
    """[Average loss across multiple batches ; Much more resistant to fluctuations.]"""
    out = {}
    model.eval()
    for split in ['train', 'eval']:
        losses = torch.zeros(eval_iters)
        for k in range(eval_iters):
            X, Y = get_batch(split)
            logits, loss = model(X, Y)
            losses[k] = loss.item()
        out[split] = losses.mean()
    model.train()
    return out


def get_batch(split):
    data = trainData if split == 'train' else valData
    ix = torch.randint(len(data) - context_length, (batch_size,))  # random offsets in the dataset.
    x = torch.stack([data[i:i+context_length] for i in ix])
    y = torch.stack([data[i+1: i+context_length+1] for i in ix])
    x, y = x.to(device), y.to(device)
    return x, y


xb, yb = get_batch('train')

for b in range(batch_size):
    for t in range(context_length):
        context = xb[b, :t+1]
        target = yb[b, t]


class BigramLanguageModel(nn.module):
    def __init__(self, vocab_size):
        super().__init__()
        self.token_embedding_table = nn.Embedding(vocab_size, vocab_size)
        self.position_embedding_table = nn.Embedding(context_length, n_embd)
        self.sa_heads = MultiHeadAttention(4, n_embd//4)   # 4 heads of 8 dimensional self attention
        self.lm_head = nn.Linear(n_embd, vocab_size)

    def forward(self, idx, targets=None):
        """[Index and targets are both batch and time(context) tensor of integers. ]"""
        # Logits are basically the scores for the next character in the sequence.
        B, T = idx.shape

        token_emb = self.token_embedding_table(idx)
        pos_emb = self.position_embedding_table(torch.arange(T))
        x = token_emb + pos_emb
        x = self.sa_heads(x)
        logits = self.lm_head(x)   # B, T, vocab_size
        # Implement the loss functions. Pytorch needs the Channel thing first.
        if targets is None:
            loss = None
        else:
            B, T, C = logits.shape
            logits = logits.view(B*T, C)
            targets = targets.view(B*T)
            loss = F.cross_entropy(logits, targets)
        return logits, loss

    def generate(self, idx, max_new_tokens):
        for _ in range(max_new_tokens):
            idx_cond = idx[:, -context_length]
            logits, loss = self(idx_cond)
            logits = logits[:, -1, :]   # (B,C)
            probs = F.softmax(logits, dims=-1)
            idx_next = torch.multinomial(probs, num_samples=1)
            idx = torch.cat((idx, idx_next), dim=1)   # from (B,T) to (B,T+1) in one iteration.
        return idx

class SelfAttentionHead(nn.Module):
    def __init__(self, head_size):
        super().__init__()
        self._key = nn.Linear(n_embd, head_size, bias=False)
        self._query = nn.Linear(n_embd, head_size, bias=False)
        self._value = nn.Linear(n_embd, head_size, bias=False)
        self.register_buffer('tril', torch.tril(torch.ones(context_length, context_length)))

    def forward(self, x):
        B, T, C = x.shape
        k = self._key(x)  # (B, T, head_size)
        q = self._query(x)  # (B, T, head_size)

        wei = q @ k.transpose(-2, -1) * C**-0.5  # divided by sqrt(head size) to control the variance.
        wei = wei.masked_fill(self.tril[:T, :T] == 0, float('-inf'))  # TODO: Do not interact with future token.
        wei = F.softmax(wei, dim=-1)  # TODO: This ensures that the distribution of tokens is normalized.

        v = self._value(x)
        out = wei @ v  # (B, T, T) @ (B, T, C) which gives (B, T, C) matrix !!
        return out


class MultiHeadAttention(nn.Module):
    """ Multiple Scaled Self Attention in parallel """
    def __init__(self, num_heads, head_size):
        super().__init__()
        self.heads = nn.ModuleList([SelfAttentionHead(head_size) for _ in range(num_heads)])
        self.proj = nn.Linear(head_size*num_heads, n_embd)
        self.dropout = nn.Dropout(dropout) 

    def forward(self, x):
        out = torch.cat([h(x) for h in self.heads], dim = -1)
        out = self.dropout(self.proj(out))
        return out
    

class FeedForward(nn.Module):
    """ a simple linear layer followed by a non-linearity """

    def __init__(self, n_embd):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_embd, 4 * n_embd),
            nn.ReLU(),
            nn.Linear(4 * n_embd, n_embd),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        return self.net(x)
    
    

class Block(nn.Module):
    """[Transformer block: communication followed by computation]"""
    def __init__(self, n_embd, n_head):
        super().__init__()
        head_size = n_embd//n_head
        self.sa = MultiHeadAttention(n_head, head_size)     # communication
        self.ffwd = FeedForward(n_embd)                    # computation

    def forward(self, x):
        x = x+ self.sa(x)
        x = x+  self.ffwd(x)
        return x

class GPTLanguageModel(nn.Module):

    def __init__(self):
        super().__init__()
        # each token directly reads off the logits for the next token from a lookup table
        self.token_embedding_table = nn.Embedding(vocab_size, n_embd)
        self.position_embedding_table = nn.Embedding(context_length, n_embd)
        self.blocks = nn.Sequential(*[Block(n_embd, n_head=n_head) for _ in range(n_layer)])
        self.ln_f = nn.LayerNorm(n_embd) # final layer norm
        self.lm_head = nn.Linear(n_embd, vocab_size)

        # better init, not covered in the original GPT video, but important, will cover in followup video
        self.apply(self._init_weights)

    def _init_weights(self, module):
        if isinstance(module, nn.Linear):
            torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                torch.nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def forward(self, idx, targets=None):
        B, T = idx.shape

        # idx and targets are both (B,T) tensor of integers
        tok_emb = self.token_embedding_table(idx) # (B,T,C)
        pos_emb = self.position_embedding_table(torch.arange(T, device=device)) # (T,C)
        x = tok_emb + pos_emb # (B,T,C)
        x = self.blocks(x) # (B,T,C)
        x = self.ln_f(x) # (B,T,C)
        logits = self.lm_head(x) # (B,T,vocab_size)

        if targets is None:
            loss = None
        else:
            B, T, C = logits.shape
            logits = logits.view(B*T, C)
            targets = targets.view(B*T)
            loss = F.cross_entropy(logits, targets)

        return logits, loss

    def generate(self, idx, max_new_tokens):
        # idx is (B, T) array of indices in the current context
        for _ in range(max_new_tokens):
            # crop idx to the last block_size tokens
            idx_cond = idx[:, -context_length:]
            # get the predictions
            logits, loss = self(idx_cond)
            # focus only on the last time step
            logits = logits[:, -1, :] # becomes (B, C)
            # apply softmax to get probabilities
            probs = F.softmax(logits, dim=-1) # (B, C)
            # sample from the distribution
            idx_next = torch.multinomial(probs, num_samples=1) # (B, 1)
            # append sampled index to the running sequence
            idx = torch.cat((idx, idx_next), dim=1) # (B, T+1)
        return idx
    
    
model = GPTLanguageModel()
m = model.to(device)
# print the number of parameters in the model
print(sum(p.numel() for p in m.parameters())/1e6, 'M parameters')

# create a PyTorch optimizer
optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)

for iter in range(max_iters):

    # every once in a while evaluate the loss on train and val sets
    if iter % eval_interval == 0 or iter == max_iters - 1:
        losses = estimate_loss()
        print(f"step {iter}: train loss {losses['train']:.4f}, val loss {losses['val']:.4f}")

    # sample a batch of data
    xb, yb = get_batch('train')

    # evaluate the loss
    logits, loss = model(xb, yb)
    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    optimizer.step()

# generate from the model
context = torch.zeros((1, 1), dtype=torch.long, device=device)
print(decode(m.generate(context, max_new_tokens=500)[0].tolist()))
#open('more.txt', 'w').write(decode(m.generate(context, max_new_tokens=10000)[0].tolist()))    
