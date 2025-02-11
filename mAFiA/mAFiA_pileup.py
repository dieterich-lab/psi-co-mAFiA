import os
import time
import pandas as pd
import pysam
from mAFiA.arg_parsers import mRNATestArgsParser
from mAFiA.output_writers import SiteWriter
from joblib import Parallel, delayed
import numpy as np

dict_mod_code = {
    'm6A': '21891',
    'psi': '17802',
    'Gm': '19229'
}


def calc_single_site(in_row, args):
    with pysam.Samfile(args.bam_file, 'r') as bam:
        chrom = in_row['chrom']
        chromStart = in_row['chromStart']
        strand = in_row['strand']
        mod_type = in_row['name']
        mod_code = int(dict_mod_code[mod_type])
        flag_require = 0 if strand == '+' else 16
        for pileupcolumn in bam.pileup(chrom, chromStart, chromStart + 1, truncate=True, flag_require=flag_require):
            if pileupcolumn.reference_pos == chromStart:
                this_site_coverage = pileupcolumn.get_num_aligned()
                if this_site_coverage >= args.min_coverage:
                    mod_probs = []
                    for pileupread in pileupcolumn.pileups:
                        flag = pileupread.alignment.flag
                        query_position = pileupread.query_position
                        if query_position is None:
                            continue
                        if flag == 16:
                            query_position = pileupread.alignment.query_length - query_position - 1
                        mod_key = ('N', 0, mod_code) if flag == 0 else ('N', 1, mod_code)
                        try:
                            sel_tup = [tup for tup in pileupread.alignment.modified_bases_forward.get(mod_key, []) if
                                       tup[0] == query_position]
                            if len(sel_tup) == 1:
                                # mod_probs.append((sel_tup[0][1] / 255.0) >= args.mod_prob_thresh)
                                mod_probs.append(sel_tup[0][1])
                        except:
                            continue
                    if len(mod_probs) >= args.min_coverage:
                        mod_probs = np.array(mod_probs) / 255.0
                        ratio = np.mean(mod_probs>=args.mod_prob_thresh)
                        conf = ((mod_probs<0.25).sum() + (mod_probs>=0.75).sum()) / len(mod_probs)
                        in_row['score'] = '.'
                        return {'in_row': in_row, 'cov': len(mod_probs), 'ratio': ratio, 'conf': conf, 'ref_5mer': in_row['ref5mer']}
        return {}


def get_bam_ref_start_end(bam_file):
    start_end = []
    with pysam.Samfile(bam_file, 'r') as bam:
        for read in bam.fetch():
            start_end.append((read.reference_start, read.reference_end))
    ref_min = min([x[0] for x in start_end])
    ref_max = max([x[1] for x in start_end])
    return (ref_min, ref_max)


def main():
    tic = time.time()

    parser = mRNATestArgsParser()
    parser.parse_and_print()
    args = parser.args

    # bam_ref_min, bam_ref_max = get_bam_ref_start_end(args.bam_file)

    if not os.path.exists(args.out_dir):
        os.makedirs(args.out_dir, exist_ok=True)
    if args.out_filename is not None:
        site_writer = SiteWriter(out_path=os.path.join(args.out_dir, args.out_filename))
    else:
        site_writer = SiteWriter(out_path=os.path.join(args.out_dir, 'mAFiA.sites.bed'))
    df_mod = pd.read_csv(args.sites, sep='\t', dtype={'chrom': str, 'chromStart': int, 'chromEnd': int}, iterator=True, chunksize=args.chunk_size)

    for chunk in df_mod:
        # if (chunk['chromStart'].values[0]<=bam_ref_max) and (chunk['chromEnd'].values[-1]>=bam_ref_min):
        this_chunk_max_coverage = []
        with pysam.AlignmentFile(args.bam_file, 'rb') as bam:
            for this_chrom in chunk['chrom'].unique():
                sub_chunk = chunk[chunk['chrom'] == this_chrom]
                # this_chunk_reads.extend(
                #     list(bam.fetch(this_chrom, sub_chunk['chromStart'].iloc[0], sub_chunk['chromEnd'].iloc[-1]))
                # )
                this_chunk_max_coverage.append(
                    np.vstack(
                        bam.count_coverage(this_chrom, sub_chunk['chromStart'].iloc[0], sub_chunk['chromEnd'].iloc[-1],
                                           quality_threshold=0)
                    ).sum(axis=0).max()
                )
        if max(this_chunk_max_coverage) >= args.min_coverage:
            sites = Parallel(n_jobs=args.num_jobs)(delayed(calc_single_site)(chunk.iloc[i], args) for i in range(len(chunk)))
            for this_site in sites:
                if this_site:
                    site_writer.update_sites(**this_site)
            site_writer.write_df(empty=True)
            print(f'{chunk.index[-1]+1} rows processed', flush=True)

    print(f'Total {site_writer.site_counts} mod. sites written to {site_writer.out_path}', flush=True)
    toc = time.time()
    print('Finished in {:.1f} mins'.format((toc - tic) / 60), flush=True)


if __name__ == "__main__":
    main()
