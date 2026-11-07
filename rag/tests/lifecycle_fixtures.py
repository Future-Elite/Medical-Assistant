"""Real PubMed XML excerpts used by lifecycle tests.

Source: NCBI E-utilities ``efetch`` (retrieved 2026-11-01).  Excerpts preserve
the real PMIDs, RefTypes, RefSource strings and publication dates; unrelated
article body/author fields are removed only to keep the fixture small.
"""

RETRACTED_GUIDELINE_XML = """\
<?xml version="1.0" ?>
<PubmedArticleSet>
<PubmedArticle>
<MedlineCitation Status="MEDLINE" Owner="NLM">
  <PMID Version="1">41110921</PMID>
  <DateCompleted><Year>2025</Year><Month>10</Month><Day>19</Day></DateCompleted>
  <Article PubModel="Print">
    <Journal>
      <JournalIssue CitedMedium="Internet">
        <Volume>41</Volume><Issue>10</Issue>
        <PubDate><Year>2025</Year><Month>Oct</Month></PubDate>
      </JournalIssue>
      <Title>The Canadian journal of cardiology</Title>
    </Journal>
    <ArticleTitle>Canadian Cardiovascular Society/Canadian Heart Failure Society 2025 Guideline Update for Pharmacologic Management of Heart Failure With Nonreduced Ejection Fraction (LVEF &gt; 40%).</ArticleTitle>
    <PublicationTypeList>
      <PublicationType UI="D016428">Journal Article</PublicationType>
      <PublicationType UI="D017065">Practice Guideline</PublicationType>
      <PublicationType UI="D016441">Retracted Publication</PublicationType>
    </PublicationTypeList>
  </Article>
  <CommentsCorrectionsList>
    <CommentsCorrections RefType="RetractionIn">
      <RefSource>Can J Cardiol. 2025 Dec 23:S0828-282X(25)01558-2. doi: 10.1016/j.cjca.2025.12.001.</RefSource>
      <PMID Version="1">41422828</PMID>
    </CommentsCorrections>
    <CommentsCorrections RefType="ErratumIn">
      <RefSource>Can J Cardiol. 2026 Sep;42(9):2039-2041. doi: 10.1016/j.cjca.2025.12.030.</RefSource>
      <PMID Version="1">41478304</PMID>
    </CommentsCorrections>
  </CommentsCorrectionsList>
</MedlineCitation>
</PubmedArticle>
</PubmedArticleSet>
"""

RETRACTION_NOTICE_XML = """\
<?xml version="1.0" ?>
<PubmedArticleSet>
<PubmedArticle>
<MedlineCitation Status="MEDLINE" Owner="NLM">
  <PMID Version="1">41422828</PMID>
  <Article PubModel="Internet">
    <Journal>
      <JournalIssue CitedMedium="Internet">
        <PubDate><Year>2025</Year><Month>Dec</Month><Day>23</Day></PubDate>
      </JournalIssue>
      <Title>The Canadian journal of cardiology</Title>
    </Journal>
    <ArticleTitle>WITHDRAWN: Corrigendum to Canadian Cardiovascular Society/Canadian Heart Failure Society 2025 Guideline Update for Pharmacologic Management of Heart Failure With Nonreduced Ejection Fraction (LVEF &gt; 40%).</ArticleTitle>
    <PublicationTypeList>
      <PublicationType UI="D016428">Journal Article</PublicationType>
    </PublicationTypeList>
  </Article>
</MedlineCitation>
</PubmedArticle>
</PubmedArticleSet>
"""

CURRENT_GUIDELINE_XML = """\
<?xml version="1.0" ?>
<PubmedArticleSet>
<PubmedArticle>
<MedlineCitation Status="MEDLINE" Owner="NLM">
  <PMID Version="1">41802129</PMID>
  <Article PubModel="Print-Electronic">
    <Journal>
      <JournalIssue CitedMedium="Internet">
        <PubDate><Year>2026</Year><Month>Jan</Month></PubDate>
      </JournalIssue>
      <Title>Neonatology</Title>
    </Journal>
    <ArticleTitle>European Consensus Guidelines on the Management of Respiratory Distress Syndrome: 2025.</ArticleTitle>
    <PublicationTypeList>
      <PublicationType UI="D016446">Consensus Statement</PublicationType>
      <PublicationType UI="D016428">Journal Article</PublicationType>
      <PublicationType UI="D017065">Practice Guideline</PublicationType>
    </PublicationTypeList>
  </Article>
</MedlineCitation>
</PubmedArticle>
</PubmedArticleSet>
"""


# Real PubMed record 33775419 (retrieved 2026-11-01): the record carries
# PublicationType="Retracted Publication" and a RetractionIn entry whose
# RefSource is a DOI only (no notice PMID).  There is therefore no dated event
# available from the cited record; the current state is still retracted.
RETRACTED_WITHOUT_DATED_NOTICE_XML = """\
<?xml version="1.0" ?>
<PubmedArticleSet>
<PubmedArticle>
<MedlineCitation Status="MEDLINE" Owner="NLM">
  <PMID Version="1">33775419</PMID>
  <Article PubModel="Print">
    <Journal>
      <JournalIssue CitedMedium="Internet">
        <PubDate><Year>2021</Year><Month>May</Month></PubDate>
      </JournalIssue>
      <Title>Revista espanola de anestesiologia y reanimacion</Title>
    </Journal>
    <ArticleTitle>RETRACTED: Anesthesic and surgical guideline for the treatment of the thoraco-abdominal aorta. Consensus Document of the Spanish Societies of Anesthesia and Cardiovascular Surgery.</ArticleTitle>
    <PublicationTypeList>
      <PublicationType UI="D017065">Practice Guideline</PublicationType>
      <PublicationType UI="D016428">Journal Article</PublicationType>
      <PublicationType UI="D016441">Retracted Publication</PublicationType>
    </PublicationTypeList>
  </Article>
  <CommentsCorrectionsList>
    <CommentsCorrections RefType="RetractionIn">
      <RefSource>10.1016/j.redar.2020.12.015</RefSource>
    </CommentsCorrections>
  </CommentsCorrectionsList>
</MedlineCitation>
</PubmedArticle>
</PubmedArticleSet>
"""
