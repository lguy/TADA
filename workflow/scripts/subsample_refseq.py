import pandas as pd
import argparse
import yaml
import sys


def parse_command_line():
    args = argparse.ArgumentParser()
    args.add_argument("--refseq-metadata", required=True)
    args.add_argument("--historical", required=True)
    args.add_argument("--names", required=True)
    args.add_argument("--nodes", required=True)
    args.add_argument("--sampling-scheme", required=True)
    args.add_argument("--output", required=True)

    return args.parse_args()


def read_names(names_path):
    names = {}
    with open(names_path, "r") as f:
        for line in f:
            line = line.strip("\n")
            line = line.split("\t|\t")
            if line[-1].strip("\t|") == "scientific name":
                names[int(line[0])] = line[1]
    return names


def read_taxonomy(nodes_path):
    taxonomy = {}
    with open(nodes_path, "r") as f:
        for line in f:
            line = line.strip("\n")
            line = line.split("|")
            taxid = int(line[0].strip("\t"))
            parent_taxid = int(line[1].strip("\t"))
            rank = line[2].strip("\t")
            if rank == "superkingdom":
                rank = "domain"
            taxonomy[taxid] = [parent_taxid, rank]

    return taxonomy

def check_taxa_name(taxa, taxa_levels, df):
    """
    Check that taxa is a valid taxa name
    """
    for level in taxa_levels:
        if taxa in df[level].to_list():
            return True
    return False

def get_taxa_level_index(taxa, taxa_levels, df):
    """
    Find the taxa level index for a taxa
    """
    for taxa_level_index, level in enumerate(taxa_levels):
        if taxa in df[level].to_list():
            return taxa_level_index

args = parse_command_line()
header = [
    "assembly_accession",
    "bioproject",
    "biosample",
    "wgs_master",
    "refseq_category",
    "taxid",
    "species_taxid",
    "organism_name",
    "infraspecific_name",
    "isolate",
    "version_status",
    "assembly_level",
    "release_type",
    "genome_rep",
    "seq_rel_date",
    "asm_name",
    "submitter",
    "gbrs_paired_asm",
    "paired_asm_comp",
    "ftp_path",
    "excluded_from_refseq",
    "relation_to_type_material",
    "asm_not_live_date",
]
assemblies_df = pd.read_csv(
    args.refseq_metadata, sep="\t", comment="#", names=header, low_memory=False
)

# Clear all entries that does not have an ftp-path
#assemblies_df = assemblies_df.dropna(subset=["ftp_path"])

assemblies_historical_df = pd.read_csv(
    args.historical,
    sep="\t",
    comment="#",
    names=header,
)

historical_accessions = assemblies_historical_df["assembly_accession"].to_list()
assemblies_df = assemblies_df[~assemblies_df["assembly_accession"].isin(historical_accessions)]


ranks = ["domain", "phylum", "class", "order", "family", "genus", "species"]
taxonomy = read_taxonomy(args.nodes)
names = read_names(args.names)
data = []
for taxid in assemblies_df["taxid"]:
    lineage = [taxid]
    curr_taxid = taxid
    reached_root = False
    while not reached_root:
        if taxonomy[curr_taxid][1] in ranks:
            lineage.append(names[curr_taxid])
        curr_taxid = taxonomy[curr_taxid][0]
        if curr_taxid == 1 or curr_taxid == 131567:
            reached_root = True

    data.append(lineage)

taxonomy_df = pd.DataFrame(data, columns=["taxid"] + ranks[::-1])
taxonomy_df.to_csv("test.taxonomy.tsv", sep="\t", index=False)
taxonomy_df.drop_duplicates(inplace=True)
df = pd.merge(left=assemblies_df, right=taxonomy_df, on="taxid")

with open(args.sampling_scheme, "r") as stream:
    sampling_scheme = yaml.safe_load(stream)

sampling_order = {}  # Store sampling parameters

if "all" in sampling_scheme.keys():
    sampling_parameters = sampling_scheme["all"]
    del sampling_scheme["all"]
    sampling_scheme["Bacteria"] = sampling_parameters
    sampling_scheme["Archaea"] = sampling_parameters
    sampling_scheme["Eukaryota"] = sampling_parameters

for taxa in sampling_scheme:
    if check_taxa_name(taxa, ranks, df):
        sampling_level = sampling_scheme[taxa]["sampling_level"]
        n_taxa = sampling_scheme[taxa]["taxa"]
        taxa_level_index = get_taxa_level_index(taxa, ranks, df)
        sampling_level_index = ranks.index(sampling_level)
        # Make sure that we not sample from a higher taxonomic level
        # compared to the given taxonomic name
        if sampling_level_index >= taxa_level_index:
            # Store sampling parameters to dictionary
            if taxa_level_index in sampling_order.keys():
                sampling_order[taxa_level_index].append([taxa, sampling_level, n_taxa])
            else:
                sampling_order[taxa_level_index] = [[taxa, sampling_level, n_taxa]]
        else:
            sys.exit("{taxa} is of rank {taxa_rank}, not possible to sample at the higher rank {sampling_rank}".format(taxa=taxa, taxa_rank=ranks[taxa_level_index], sampling_rank=sampling_level))
    else:
        sys.exit("{taxa} is not a valid taxonomic name in RefSeq".format(taxa=taxa))

# Reorder the sampling dictionary so tha we start sampling from species level and the continue
# with higher levels
sampling_order = {
    key: sampling_order[key] for key in sorted(sampling_order.keys(), reverse=True)
}
sampled_dfs = []  # Store sampled records
used_data = []  # Store data that has already been used to sample from.
for taxa_level_index in sampling_order.keys():
    taxa_level = ranks[taxa_level_index]
    for sampling in sampling_order[taxa_level_index]:
        # Extract sampling parameters
        taxa = sampling[0]
        sampling_level = sampling[1]
        n_taxa = sampling[2]

        # Create a dataframe to sample from based on the selected taxa
        sampling_df = df[df[taxa_level] == taxa]

        # Remove data that has already been used to sample fromh
        for used_df in used_data:
            sampling_df = pd.concat([sampling_df, used_df]).drop_duplicates(keep=False)

        # Group the sampling dataframe based on the sampling level
        for i, taxa_level_df in sampling_df.groupby(sampling_level):

            # Can't take a sample if the sample size we ask for is larger than
            # the number of taxa in that group. In that case, use all taxa in
            # the group.
            if taxa_level_df.shape[0] > n_taxa:
                sampled_dfs.append(taxa_level_df.sample(n_taxa))
            else:
                sampled_dfs.append(taxa_level_df)

        # Finally, add the sampling dataframe to used data
        used_data.append(sampling_df)

sampled_df = pd.concat(sampled_dfs)
sampled_df.to_csv(args.output, sep='\t', index=False)