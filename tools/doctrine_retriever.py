from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import chromadb
from chromadb.utils import embedding_functions

logger = logging.getLogger(__name__)

# Modelo de embeddings. DEBE coincidir con el usado en build_index.py,
# de lo contrario las dimensiones del vector de consulta no encajan con
# las de la coleccion (p. ej. 768 de mpnet frente a 384 de MiniLM).
_DEFAULT_EMBED_MODEL = "paraphrase-multilingual-mpnet-base-v2"


@dataclass
class RetrievedDocument:
    page_content: str
    metadata: dict[str, Any]


class DoctrineRetriever:
    """Retriever semantico sobre chunks de doctrina, usando ChromaDB con
    embeddings multilingues (paraphrase-multilingual-mpnet-base-v2).

    Sustituye a la version anterior basada en interseccion de tokens lexicos,
    que fallaba al recibir consultas en espanol sobre un corpus indexado en
    ingles (ver limitacion documentada en el capitulo de evaluacion).

    La coleccion se abre con la misma embedding function multilingue que se
    registro en la indexacion. Asi ChromaDB genera el vector de la consulta
    con el modelo correcto (768 dim) y no con su funcion por defecto (384 dim),
    evitando el conflicto de dimensiones.
    """

    def __init__(
        self,
        chroma_dir: str = "rag/data/chroma",
        collection: str = "ajp_doctrine_chunks",
        embedding_model: str | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            chroma_dir: ruta al directorio persistente de ChromaDB.
            collection: nombre de la coleccion dentro de ChromaDB.
            embedding_model: nombre del modelo SentenceTransformer. Debe
                coincidir con el usado en build_index.py.
        """
        self.chroma_dir = chroma_dir
        self.collection_name = collection
        self.embedding_model = embedding_model or _DEFAULT_EMBED_MODEL

        self._embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=self.embedding_model
        )
        self.client = chromadb.PersistentClient(path=chroma_dir)
        self.collection = self.client.get_collection(
            collection,
            embedding_function=self._embed_fn,
        )

    def retrieve(
        self,
        query: str,
        k: int = 5,
        filter_meta: dict[str, Any] | None = None,
    ) -> dict[str, list[RetrievedDocument]]:
        """
        Recupera los k chunks mas relevantes mediante similitud semantica.

        Args:
            query: consulta en lenguaje natural, en cualquier idioma soportado
                por el modelo de embeddings (incluye espanol e ingles).
            k: numero maximo de chunks a devolver.
            filter_meta: filtros de metadata. Soporta {"source_doc": "AJP-3.X"}
                para restringir la busqueda a un documento concreto.

        Returns:
            {"docs_final": [RetrievedDocument, ...]}
        """
        where_filter = None
        if filter_meta and filter_meta.get("source_doc"):
            where_filter = {"source_doc": {"$eq": filter_meta["source_doc"]}}

        try:
            result = self.collection.query(
                query_texts=[query],
                n_results=k,
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.error("ChromaDB retrieval error: %s", e)
            return {"docs_final": []}

        docs: list[RetrievedDocument] = []
        documents = result.get("documents") or [[]]
        metadatas = result.get("metadatas") or [[]]
        distances = result.get("distances") or [[]]

        for text, metadata, distance in zip(
            documents[0], metadatas[0], distances[0]
        ):
            metadata = dict(metadata or {})
            # ChromaDB devuelve distancia coseno (menor = mas similar).
            # Se convierte a un score de similitud en (0, 1], mayor = mejor.
            metadata["score"] = 1.0 / (1.0 + distance)
            docs.append(RetrievedDocument(text, metadata))

        logger.info(
            "Retriever: query=%r source_doc=%s chunks=%d",
            query[:60],
            (filter_meta or {}).get("source_doc", "any"),
            len(docs),
        )

        return {"docs_final": docs}