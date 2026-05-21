from tools.doctrine_retriever import DoctrineRetriever
r = DoctrineRetriever()
docs = r.db.similarity_search("test", k=1)
print(docs[0].metadata)