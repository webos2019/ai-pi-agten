import pandas as pd
df=pd.DataFrame({
    "age":[22,45],
    "sex":["male","female"]
 })
df.loc[df["age"]>30,"sex"]="old"
print(df.loc[0,"age"])
