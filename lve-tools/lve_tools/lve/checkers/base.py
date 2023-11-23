import re
from collections import defaultdict 
from lve.prompt import Role

def extract_variables_from_prompt(prompt):
    variables = {}
    for msg in prompt:
        if msg.role == Role.assistant:
            varname = msg.variable
            if varname is None:
                varname = str(len(variables))
            variables[varname] = msg.content
    return variables

def extract_response_from_prompt(prompt):
    response = ""
    assert prompt[-1].role == Role.assistant
    return prompt[-1].content

class CheckerRegistryHolder(type):

    CHECKER_REGISTRY = {}

    def __new__(cls, name, bases, attrs):
        new_cls = type.__new__(cls, name, bases, attrs)
        if "BaseChecker" not in name:
            cls.CHECKER_REGISTRY[name] = new_cls
        return new_cls

    @classmethod
    def get_checker_registry(cls):
        return dict(cls.CHECKER_REGISTRY)
    


class BaseChecker(metaclass=CheckerRegistryHolder):
    
    def __init__(self, prompt_contains_responses=False, get_variables=False, get_response=True):
        self.prompt_contains_responses = prompt_contains_responses
        self.get_variables = get_variables
        self.get_response = get_response 
        
    def invoke_check(self, prompt_in, prompt_out, param_values=None):
        """ Function called by LVE to invoke the checker.
            Main function exposed to the lve internals, but not the user.
            
            Args:
                prompt_in: The chat corresponding to the input. Currently unused.
                prompt_out: The chat including model responses.
                param_values: The parameter values plugged into the prompt.
            
            Returns:
                is_safe: Whether the response is safe as determined by is_safe.
                response_or_variables: If there is only one response, returns the response as string, else returns all variables as a dictionary.
                score: an optional score for the response
        """
        cnt_variables = sum(p.role == Role.assistant and p.variable is not None for p in prompt_out)
        is_safe = self.is_safe(prompt_out, param_values)
        
        if cnt_variables > 1:
            response_or_variables = extract_variables_from_prompt(prompt_out) 
        else:
            response_or_variables = extract_response_from_prompt(prompt_out)

        score = None
        if self.has_scoring():
            score = self.calculate_score(prompt_out, param_values)
            
        return is_safe, response_or_variables, score

    def is_safe(self, prompt_out, param_values=None) -> bool:
        """Determines whether the response is safe given the prompt and the parameters.

        Args:
            prompt_out: The prompt including the model response.
            param_values: The parameter values plugged into the prompt.
        """
        raise NotImplementedError

    def postprocess_response(self, response):
        """Postprocess response (e.g. to remove bad words or PII)

        Args:
            response: The response generated by the model.
        """
        return response
    
    def calculate_score(self, prompt_out, param_values=None) -> float:
        raise NotImplementedError
    
    # meta information about the checker

    @classmethod
    def has_post_processing(cls):
        """Whether the checker has post processing."""
        return not cls.postprocess_response.__code__ == BaseChecker.postprocess_response.__code__
    
    @classmethod
    def has_scoring(cls):
        """Whether the checker support scoring."""
        return not cls.calculate_score.__code__ == BaseChecker.calculate_score.__code__

    @classmethod 
    def is_multi_run(cls):
        """Whether the checker is a multi-run checker."""
        return False

    @classmethod
    def is_multi_variable(cls):
        """Whether the checker is a multi-variable checker."""
        return False

class LambdaChecker(BaseChecker):
    """
    Checker which uses a lambda function to check safety.

    **Attributes**

    - *func*: The lambda function to be executed. Should return a `bool`.
    """

    def __init__(self, func, score=None):
        super().__init__()
        self.func = eval(func)
        if score is not None:
            self.score_fn = eval(score)
        else:
            self.score_fn = None

        
    def is_safe(self, prompt_out, param_values=None) -> bool:
        response = extract_response_from_prompt(prompt_out)
        return self.func(response, **param_values)

    def calculate_score(self, prompt_out, param_values=None) -> float:
        if self.score_fn is not None:
            response = extract_response_from_prompt(prompt_out)
            return self.score_fn(response, **param_values)
        else:
            return None

class RegexChecker(BaseChecker):
    """
    Checker which judges safety based on whether the response matches given pattern.

    **Attributes**    

    - *pattern*: The regex pattern.
    - *match_safe*: The value of the match to be compared against.
    - *flags*: A string containing one Regex Flag. Currently only `A`, `I`, `L`, `M`, `DOTALL` are supported. Defaults to 0 (no flag).
    """

    def get_flag(self, flag):
        if flag == "A" or flag == "ASCII":
            return re.ASCII
        elif flag == "I" or flag == "IGNORECASE":
            return re.IGNORECASE
        elif flag == "L" or flag == "LOCALE":
            return re.LOCALE
        elif flag == "M" or flag == "MULTILINE":
            return re.MULTILINE
        elif flag == 'DOTALL':
            return re.DOTALL
        
        raise ValueError(f"Unknown regex flag {flag}")

    def __init__(self, pattern, match_safe, flags=0):
        super().__init__()
        
        if flags != 0:
            flags = self.get_flag(flags)

        self.pattern = re.compile(pattern, flags=flags)
        self.match_safe = match_safe
    
    def is_safe(self, prompt_out, param_values=None) -> bool:
        response = extract_response_from_prompt(prompt_out)
        matches = self.pattern.search(response) is not None
        return matches == self.match_safe
    
    def __str__(self):
        return f"RegexChecker(pattern={self.pattern.pattern}, match_safe={self.match_safe}, flags={self.pattern.flags})"

class MultiRunBaseChecker(BaseChecker):

    def invoke_check(self, prompts_in, prompts_out, param_values=None):
        """ Function called by LVE to invoke the checker.
            Main function exposed to the lve internals, but not the user.
            
            Args:
                prompts_in: List of the chats corresponding to the inputs.
                prompts_out: List of the chats including the model responses. Order should match prompts_in.
                param_values: The parameter values plugged into the prompt.

            Returns:
                is_safe: Whether the response is safe as determined by is_safe.
                response_or_variables: If there is only one response, returns the response as string, else returns all variables as a dictionary.
                score: an optional score for the response
        """
        assert len(prompt_in) == len(prompt_out)
        cnt_variables = sum(p.role == Role.assistant and p.variable is not None for p in prompt_out[0])
        is_safe = self.is_safe(prompt_out, param_values)
        
        response_or_variables = response
        if cnt_variables > 1:
            response_or_variables = extract_variables_from_prompt(prompt_out) 

        score = None
        if self.has_scoring():
            score = self.calculate_score(prompt_out, param_values)
            
        return is_safe, response_or_variables, score

    @classmethod 
    def is_multi_run(cls):
        return True
    
class MultiRunLambdaChecker(MultiRunBaseChecker):
    """
    Checker which uses a lambda function to check safety.

    **Attributes**

    - *func*: The lambda function to be executed. Should return a `bool`.
    """

    def __init__(self, func):
        super().__init__()
        self.func = eval(func)
        
    def is_safe(self, prompts_out, param_values) -> bool:
        responses = [extract_response_from_prompt(p) for p in prompts_out]
        return self.func(response, **param_values)


