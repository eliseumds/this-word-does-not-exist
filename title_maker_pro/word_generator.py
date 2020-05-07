import torch
import logging
import stanza
import title_maker_pro.datasets as datasets
import title_maker_pro.modeling as modeling
from transformers import AutoModelWithLMHead, AutoTokenizer

logger = logging.getLogger(__name__)


class WordGenerator:
    def __init__(self, forward_model_path, inverse_model_path, blacklist_path, quantize=False, device=None):
        if not device:
            self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        stanza.download("en")
        self.stanza_pos_pipeline = stanza.Pipeline(
            lang="en", processors="tokenize,mwt,pos", use_gpu=("cpu" not in self.device.type)
        )

        logger.info(f"Using device {self.device}")

        logger.info(f"Loading word blacklist from {blacklist_path}...")
        self.blacklist = datasets.Blacklist.load(blacklist_path)
        logger.info(f"Loaded {len(self.blacklist)} words to blacklist")

        logger.info("Loading GPT2 tokenizer...")
        self.tokenizer = AutoTokenizer.from_pretrained("gpt2")
        self.tokenizer.add_special_tokens(datasets.SpecialTokens.special_tokens_dict())
        logger.info("Loaded tokenizer")

        ml = modeling.load_model
        if quantize:
            ml = modeling.load_quantized_model
            logger.info(f"Peforming quantization on models")

        logger.info(f"Loading forward model from {forward_model_path}")
        self.forward_model = ml(AutoModelWithLMHead, forward_model_path).to(self.device)
        logger.info("Loaded forward model")

        logger.info(f"Loading inverse model from {inverse_model_path}")
        self.inverse_model = ml(AutoModelWithLMHead, inverse_model_path).to(self.device)
        logger.info("Loaded inverse model")

        self.approx_max_length = 250

    def generate_word(self, user_filter=None):
        expanded, _ = datasets.ParsedDictionaryDefinitionDataset.generate_words(
            self.tokenizer,
            self.forward_model,
            num=1,
            max_iterations=5,
            blacklist=self.blacklist,
            generation_args=dict(
                top_k=300, num_return_sequences=10, max_length=self.approx_max_length, do_sample=True,
            ),
            num_expansion_candidates=20,
            example_match_pos_pipeline=self.stanza_pos_pipeline,
            user_filter=user_filter,
            dedupe_titles=True,
            filter_proper_nouns=True,
            use_custom_generate=True,
        )

        return expanded[0] if expanded else None

    def generate_definition(self, word, user_filter=None):
        prefix = f"{datasets.SpecialTokens.BOS_TOKEN}{word}{datasets.SpecialTokens.POS_SEP}"
        expanded, stats = datasets.ParsedDictionaryDefinitionDataset.generate_words(
            self.tokenizer,
            self.forward_model,
            num=1,
            prefix=prefix,
            max_iterations=3,
            generation_args=dict(top_k=75, num_return_sequences=6, max_length=self.approx_max_length, do_sample=True,),
            example_match_pos_pipeline=self.stanza_pos_pipeline,
            dedupe_titles=False,
            user_filter=user_filter,
            filter_proper_nouns=False,
            use_custom_generate=True,
        )

        logger.info(f"Generation stats: {stats}")

        if expanded:
            return expanded[0]
        else:
            return None

    def generate_word_from_definition(self, definition, user_filter=None):
        # Data peculiarity: definitions ending in a period are out of domain
        prefix = f"{datasets.SpecialTokens.BOS_TOKEN}{definition.rstrip('. ')}{datasets.SpecialTokens.DEFINITION_SEP}"
        expanded, stats = datasets.InverseParsedDictionaryDefinitionDataset.generate_words(
            self.tokenizer,
            self.inverse_model,
            blacklist=self.blacklist,
            num=1,
            prefix=prefix,
            max_iterations=5,
            generation_args=dict(top_k=200, num_return_sequences=5, max_length=self.approx_max_length, do_sample=True,),
            dedupe_titles=True,
            user_filter=user_filter,
        )

        logger.debug(stats)

        if expanded:
            return expanded[0]
        else:
            return None
