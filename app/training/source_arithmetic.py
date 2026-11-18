"""Evaluate only explicit numeric arithmetic, never executable Excel functions."""
import ast
import re
from decimal import Decimal


def evaluate(formula, values):
    if not isinstance(formula,str) or not formula.startswith('=') or len(formula)>500:return None
    refs={}
    def replace(match):
        key=match.group().replace('$','').upper()
        if key not in values or isinstance(values[key],bool) or not isinstance(values[key],(int,float,Decimal)):raise ValueError
        name='cell_'+key;refs[name]=Decimal(str(values[key]));return name
    try:
        expression=re.sub(r'(?<![A-Za-z0-9_])\$?[A-Za-z]{1,3}\$?[1-9][0-9]*(?![A-Za-z0-9_])',replace,formula[1:]).replace(';',',').replace('<>','!=')
        expression=re.sub(r'(?<![<>!=])=(?!=)','==',expression)
        tree=ast.parse(expression,mode='eval')
        if sum(1 for _ in ast.walk(tree))>180:return None
        def visit(node):
            if isinstance(node,ast.Expression):return visit(node.body)
            if isinstance(node,ast.Name) and node.id in refs:return refs[node.id]
            if isinstance(node,ast.Constant) and type(node.value) in (int,float):return Decimal(str(node.value))
            if isinstance(node,ast.Constant) and isinstance(node.value,str) and len(node.value)<150:return node.value
            if isinstance(node,ast.Compare) and len(node.ops)==1:
                left,right=visit(node.left),visit(node.comparators[0]);op=node.ops[0]
                if isinstance(op,ast.Lt):return left<right
                if isinstance(op,ast.LtE):return left<=right
                if isinstance(op,ast.Gt):return left>right
                if isinstance(op,ast.GtE):return left>=right
                if isinstance(op,ast.Eq):return left==right
                if isinstance(op,ast.NotEq):return left!=right
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and not node.keywords:
                name=node.func.id.upper()
                if name=='IF' and len(node.args)==3:return visit(node.args[1] if visit(node.args[0]) else node.args[2])
                if name=='AND':return all(visit(a) for a in node.args)
                if name=='OR':return any(visit(a) for a in node.args)
            if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):return visit(node.operand)*(1 if isinstance(node.op,ast.UAdd) else -1)
            if isinstance(node,ast.BinOp):
                left,right=visit(node.left),visit(node.right)
                if isinstance(node.op,ast.Add):return left+right
                if isinstance(node.op,ast.Sub):return left-right
                if isinstance(node.op,ast.Mult):return left*right
                if isinstance(node.op,ast.Div) and right:return left/right
            raise ValueError
        result=visit(tree)
        if isinstance(result,str):return result
        return result if isinstance(result,Decimal) and result.is_finite() and abs(result)<=1000000 else None
    except (ValueError,SyntaxError,ArithmeticError,TypeError):return None
